#!/usr/bin/env python3
"""
Mnemonic Proxy Manager for llama.cpp model manager.

This module provides the MnemonicProxyManager class for managing mnemonic-proxy
processes that sit between AI agents and llama-server instances, providing
long-context management with verbatim archive, recall, and saved engine state.

Architecture: AI Agents → mnemonic-proxy (port 8096) → llama-server (port 9931+)

Requirements:
- mnemonic-proxy Python package (pip install mnemonic-proxy)
- llama-server with ctx_size >= 16384 (32768 recommended)
- Supports saved state via --slot-save-path
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import requests

from webapp.config import (
    BASE_DIR,
    DEFAULTS,
    WINDOWS,
    WSL,
    convert_path_for_binary,
    resolve_model_path,
)
from webapp.models import InvalidModeError, get_models

log = logging.getLogger("llamaweb.mnemonic_proxy")

# ── Startup tuning ──────────────────────────────────────────────────────────
# The proxy's own /health endpoint makes a synchronous request to the upstream
# llama-server (it reports the engine's health too). A freshly started model
# (warmup, /props + /tokenize probes, slot restore, 30B MoE under load) can take
# a while to answer, so a single short-timeout health check right after spawn
# produces false "failed to start" errors. Poll patiently instead.
PROXY_START_TIMEOUT = float(os.getenv("MNEMONIC_PROXY_START_TIMEOUT", "30"))
PROXY_START_POLL_INTERVAL = float(os.getenv("MNEMONIC_PROXY_START_POLL_INTERVAL", "0.5"))
# Steady-state checks (status endpoints) fail fast; startup polling is more
# patient because the proxy's /health forwards to a possibly busy engine.
PROXY_HEALTH_TIMEOUT = float(os.getenv("MNEMONIC_PROXY_HEALTH_TIMEOUT", "2.0"))
PROXY_START_HEALTH_TIMEOUT = float(os.getenv("MNEMONIC_PROXY_START_HEALTH_TIMEOUT", "5.0"))
MCP_START_TIMEOUT = float(os.getenv("MNEMONIC_MCP_START_TIMEOUT", "15"))

# Config keys that are always derived from the live model configuration and must
# never be clobbered by a stale config_override (e.g. copy-pasted between models).
# These are either passed as CLI args (upstream) or managed by the webapp
# (mcp_port) and must not be written into mnemonic-proxy's config.json.
_DERIVED_CONFIG_KEYS = ("upstream", "slot_dir", "mcp_port")


def _needs_zstd_shim() -> bool:
    """
    True if this interpreter lacks the stdlib `compression.zstd` module.

    ctxproxy's kv_archive (cold archive of saved-state files) requires it and
    refuses to start when it is missing.
    """
    if sys.version_info >= (3, 14):
        return False
    try:
        import compression.zstd  # noqa: F401
        return False
    except ImportError:
        return True


def _zstd_shim_usable() -> bool:
    """
    True if compression_zstd_shim can provide compression.zstd here.

    The shim wraps the `zstandard` PyPI package with the stdlib API, but it has
    to be imported *inside the mnemonic-proxy process* (before
    ctxproxy.kvarchive) - importing it in the web app has no effect on the
    child process.
    """
    if not _needs_zstd_shim():
        return True
    if not (BASE_DIR / "webapp" / "compression_zstd_shim.py").exists():
        return False
    try:
        import zstandard  # noqa: F401
    except ImportError:
        return False
    return True


def _script_interpreter(binary: Path) -> str:
    """Interpreter that runs a console script (its shebang, else sys.executable)."""
    try:
        with open(binary, "r", encoding="utf-8", errors="replace") as f:
            first_line = f.readline()
        if first_line.startswith("#!"):
            interpreter = first_line[2:].strip().split()
            if interpreter:
                return interpreter[0]
    except OSError:
        pass
    return sys.executable


def _validate_local_mode() -> None:
    """
    Validate that we're in local mode.
    
    Raises:
        InvalidModeError: If REMOTE_MODE is enabled
    """
    import os
    remote_mode = os.getenv("REMOTE_MODE", "").strip().lower() in ("true", "1", "yes", "on")
    if remote_mode:
        raise InvalidModeError("mnemonic-proxy lifecycle operations")


class MnemonicProxyManager:
    """
    Manages mnemonic-proxy processes for long-context AI agent support.
    
    The proxy sits between AI agents (Claude Code, Codex, etc.) and llama-server,
    providing context management with verbatim archive, recall, and saved state.
    
    Attributes:
        model_name: Name of the model this proxy serves
        llama_port: Port of the upstream llama-server
        proxy_port: Port for the mnemonic-proxy (default 8096)
        data_dir: Directory for proxy data storage
        slot_dir: Directory for slot persistence (matches llama-server --slot-save-path)
        tokenizer_path: Optional path to tokenizer for accurate token counting
        config_override: Optional config overrides for mnemonic-proxy
        mcp_enabled: Whether to enable MCP server (default False)
        mcp_port: Port for MCP server (default 8133)
    """
    
    def __init__(
        self,
        model_name: str,
        llama_port: int,
        proxy_port: int = 8096,
        data_dir: str = "",
        slot_dir: str = "",
        tokenizer_path: str = "",
        config_override: Optional[dict] = None,
        mcp_enabled: bool = False,
        mcp_port: int = 8133,
    ):
        """
        Initialize the MnemonicProxyManager.
        
        Args:
            model_name: Name of the model this proxy serves
            llama_port: Port of the upstream llama-server
            proxy_port: Port for the mnemonic-proxy (default 8096)
            data_dir: Directory for proxy data storage (default: ./data/{model_name})
            slot_dir: Directory for slot persistence (default: from model's slot_save_path)
            tokenizer_path: Optional path to tokenizer for accurate token counting
            config_override: Optional config overrides for mnemonic-proxy
            mcp_enabled: Whether to enable MCP server (default False)
            mcp_port: Port for MCP server (default 8133)
        """
        _validate_local_mode()
        
        self.model_name = model_name
        self.llama_port = llama_port
        self.proxy_port = proxy_port
        self.data_dir = data_dir or f"./data/{model_name}"
        self.slot_dir = slot_dir
        self.tokenizer_path = tokenizer_path
        self.config_override = config_override or DEFAULTS["mnemonic_proxy"]["config_override"]
        self.mcp_enabled = mcp_enabled
        self.mcp_port = mcp_port
        
        # Process tracking
        self._pid: Optional[int] = None
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._start_time: Optional[float] = None
        self._starting = False
        self._log_handle = None  # open file object for proxy stdout/stderr
        self._last_health: Optional[str] = None

        # MCP server tracking
        self._mcp_pid: Optional[int] = None
        self._mcp_process: Optional[subprocess.Popen] = None
        self._mcp_lock = threading.Lock()
        self._mcp_start_time: Optional[float] = None
        self._mcp_log_handle = None

        # Ensure data directory exists
        self._ensure_data_dir()

    @property
    def log_path(self) -> Path:
        """Path of the proxy's captured stdout/stderr log file."""
        return self._resolve_data_dir() / "proxy.log"
    
    def _ensure_data_dir(self) -> None:
        """Ensure the data directory exists."""
        data_path = Path(self.data_dir)
        if not data_path.is_absolute():
            data_path = BASE_DIR / self.data_dir
        data_path.mkdir(parents=True, exist_ok=True)
        log.debug(f"Ensured data directory exists: {data_path}")
    
    def _get_mnemonic_proxy_binary(self) -> Optional[Path]:
        """
        Find the mnemonic-proxy binary/executable.
        
        Returns:
            Path to mnemonic-proxy executable or None if not found
        """
        # Check common locations
        candidates = [
            Path(shutil.which("mnemonic-proxy")) if shutil.which("mnemonic-proxy") else None,
            BASE_DIR / "mnemonic-proxy",
            BASE_DIR / "mnemonic-proxy.exe",
            Path.home() / ".local" / "bin" / "mnemonic-proxy",
            Path("/usr/local/bin/mnemonic-proxy"),
            # Check venv bin directory
            BASE_DIR / "venv" / "bin" / "mnemonic-proxy",
            BASE_DIR / "venv" / "bin" / "mnemonic-proxy.exe",
        ]
        
        for candidate in candidates:
            if candidate and candidate.exists():
                return candidate
        
        return None
    
    def build_cmd(self) -> list[str]:
        """
        Build the command for starting mnemonic-proxy.
        
        Returns:
            List of command arguments
        """
        binary = self._get_mnemonic_proxy_binary()
        if not binary:
            raise RuntimeError("mnemonic-proxy not found. Install with: pip install mnemonic-proxy")
        
        # Detect engine
        engine = self._detect_engine()
        
        # Resolve data directory
        data_dir = self._resolve_data_dir()
        
        # Write fresh config file so proxy uses the verified n_ctx / window size.
        # _apply_runtime_requirements drops features this interpreter cannot
        # support (e.g. kv_archive without compression.zstd).
        config = self._apply_runtime_requirements(self.generate_config())
        config_path = self._write_config_file(config)
        
        # Launcher prefix: empty in the normal case (console script is exec'd
        # directly); on Python < 3.14 with kv_archive it preloads the zstd shim
        # in the proxy process, without which the proxy exits immediately.
        cmd = self._launcher_prefix(binary, config)
        if not cmd:
            # With the preloader the script path is embedded in the snippet
            # (sys.argv[0]); passing it again would look like a stray argument.
            cmd = [str(binary)]
        
        # Core proxy settings - pass data dir and config explicitly
        cmd += [
            "--port", str(self.proxy_port),
            "--host", "0.0.0.0",
            "--engine", engine,
            "--upstream", f"http://127.0.0.1:{self.llama_port}",
            "--data", str(data_dir),
            "--config", str(config_path),
        ]
        
        # Add tokenizer if available
        tokenizer = self.tokenizer_path or self._auto_detect_tokenizer()
        if tokenizer:
            tokenizer = convert_path_for_binary(Path(tokenizer))
            cmd += ["--tokenizer", tokenizer]
        
        log.info(f"Built mnemonic-proxy command for {self.model_name}: {cmd}")
        return cmd
    
    def _resolve_data_dir(self) -> Path:
        """Resolve data directory to absolute path."""
        data_path = Path(self.data_dir)
        if not data_path.is_absolute():
            data_path = BASE_DIR / self.data_dir
        return data_path
    
    def _get_model_config(self) -> Optional[dict]:
        """Get the model configuration from models.json."""
        models = get_models()
        return models.get(self.model_name)
    
    def _auto_detect_tokenizer(self) -> Optional[str]:
        """
        Auto-detect tokenizer.json next to the model file.
        
        Returns:
            Path to tokenizer.json if found, None otherwise
        """
        model_config = self._get_model_config()
        if not model_config:
            return None
        
        model_path_str = model_config.get("model_path", "")
        if not model_path_str:
            return None
        
        model_path = resolve_model_path(model_path_str)
        if not model_path or not model_path.exists():
            return None
        
        # Look for tokenizer.json in the same directory as the model
        model_dir = model_path.parent
        tokenizer_path = model_dir / "tokenizer.json"
        if tokenizer_path.exists():
            log.debug(f"Auto-detected tokenizer: {tokenizer_path}")
            return str(tokenizer_path)
        
        # Also check for tokenizer.model (sentencepiece)
        tokenizer_model = model_dir / "tokenizer.model"
        if tokenizer_model.exists():
            log.debug(f"Auto-detected tokenizer.model: {tokenizer_model}")
            return str(tokenizer_model)
        
        return None
    
    def _detect_engine(self) -> str:
        """
        Detect the mnemonic-proxy engine based on model's spec_type.
        
        Valid mnemonic-proxy engines: auto, strata, llama.cpp, ds4, openai
        
        Returns:
            Engine name for mnemonic-proxy
        """
        model_config = self._get_model_config()
        if not model_config:
            return "llama.cpp"
        
        spec_type = model_config.get("spec_type", "none")
        
        # All llama.cpp models use the llama.cpp engine
        # The 'auto' option also works for auto-detection
        return "llama.cpp"
    
    def generate_config(self) -> dict:
        """
        Generate mnemonic-proxy configuration from model settings.
        
        v0.3.24+ uses minimal config for llama.cpp engine - window is auto-discovered
        from the upstream engine. We only write what the proxy cannot discover:
        - slot_dir (for saved state, must match llama-server --slot-save-path)
        - tokenizer (for accurate token counting)
        - config_override (advanced settings)
        
        Returns:
            Configuration dictionary for mnemonic-proxy
        """
        model_config = self._get_model_config() or {}

        # config_override may carry stale per-model values (e.g. copy-pasted
        # between models in models.json). Keys that are derived from the live
        # model configuration must never be clobbered by them.
        override = dict(self.config_override or {})
        stale = {k: override.pop(k) for k in _DERIVED_CONFIG_KEYS if k in override}

        # Get slot directory from model's slot_save_path (required for saved state)
        slot_save_path = model_config.get("slot_save_path", "")
        # Precedence: explicit arg > model's slot_save_path > (stale) override value
        slot_dir = self.slot_dir or slot_save_path or stale.get("slot_dir", "")

        # Convert slot_dir for binary if needed (WSL path conversion)
        if slot_dir:
            slot_dir = convert_path_for_binary(Path(slot_dir))

        # Detect engine
        engine = self._detect_engine()

        # Auto-detect tokenizer
        tokenizer = self.tokenizer_path or self._auto_detect_tokenizer()
        if tokenizer:
            tokenizer = convert_path_for_binary(Path(tokenizer))

        # Build minimal config - v0.3.24+ auto-discovers window from engine for llama.cpp
        # Only write what the proxy cannot discover. Note: `upstream` and
        # `mcp_port` are webapp-managed values (passed as CLI args to the
        # mnemonic-proxy / mnemonic-mcp binaries) and must NOT be written into
        # mnemonic-proxy's config.json.
        config = {
            "engine": engine,
        }

        # Add slot_dir if available (required for saved state to work)
        if slot_dir:
            config["slot_dir"] = slot_dir

        # Add tokenizer if available (for accurate token counting)
        if tokenizer:
            config["tokenizer"] = tokenizer

        # Warn about ignored stale overrides so models.json can be cleaned up
        derived_values = {
            "upstream": f"http://127.0.0.1:{self.llama_port}",
            "slot_dir": slot_dir,
            "mcp_port": self.mcp_port,
        }
        for key, value in stale.items():
            derived = derived_values.get(key)
            if value != derived:
                log.warning(
                    f"Ignoring stale config_override[{key}]={value!r} for "
                    f"{self.model_name}; using {derived!r} derived from the model config"
                )

        # Merge config_override (allows overriding any setting including window)
        if override:
            config.update(override)

        log.info(f"Generated mnemonic-proxy config for {self.model_name}: {config}")
        return config
    
    def _apply_runtime_requirements(self, config: dict) -> dict:
        """
        Make the generated config runnable on this interpreter.

        kv_archive needs the stdlib compression.zstd module (Python >= 3.14);
        without it mnemonic-proxy exits immediately. On older Pythons the shim
        provides the module (see _launcher_prefix); if that is unavailable too,
        kv_archive is switched off with a warning - the rest of the proxy
        (masking, verbatim archive, recall, saved state) keeps working.

        Args:
            config: Config as produced by generate_config()

        Returns:
            The config to write to disk (possibly with kv_archive disabled)
        """
        if not config.get("kv_archive"):
            return config

        if _zstd_shim_usable():
            return config

        config = dict(config)
        config["kv_archive"] = False
        log.warning(
            f"kv_archive disabled for {self.model_name}: compression.zstd needs "
            f"Python >= 3.14 (running {sys.version_info.major}.{sys.version_info.minor}) "
            f"and the compression_zstd_shim/zstandard fallback is unavailable. "
            f"Install the 'zstandard' package or set kv_archive=false in models.json."
        )
        return config

    def _launcher_prefix(self, binary: Path, config: dict) -> list[str]:
        """
        Command prefix used to start the mnemonic-proxy binary.

        Empty in the normal case (the console script is exec'd directly). When
        kv_archive is enabled on an interpreter without compression.zstd, the
        shim must be imported before ctxproxy.kvarchive inside the proxy
        process, so the binary is launched through a small Python preloader.
        """
        if not (config.get("kv_archive") and _needs_zstd_shim()):
            return []

        preload = (
            f"import sys; sys.path.insert(0, {str(BASE_DIR / 'webapp')!r}); "
            f"import compression_zstd_shim; "
            f"sys.argv[0] = {str(binary)!r}; "
            f"from ctxproxy.server import main; sys.exit(main())"
        )
        log.info(
            f"Preloading compression_zstd_shim for {self.model_name} "
            f"(kv_archive on Python {sys.version_info.major}.{sys.version_info.minor})"
        )
        return [_script_interpreter(binary), "-c", preload]

    def _write_config_file(self, config: Optional[dict] = None) -> Path:
        """
        Write the mnemonic-proxy config to a JSON file in the data directory.

        Args:
            config: Config to write; generated when omitted

        Returns:
            Path to the written config file
        """
        if config is None:
            config = self.generate_config()
        config_path = self._resolve_data_dir() / "config.json"
        
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        
        log.debug(f"Wrote mnemonic-proxy config to {config_path}")
        return config_path
    
    def start(self, timeout: float = PROXY_START_TIMEOUT) -> bool:
        """
        Start the mnemonic-proxy process.

        The proxy's /health endpoint reports the *upstream engine's* health as
        well, so a freshly started (or busy) llama-server can keep it non-200
        for a while. We therefore poll patiently instead of doing a single
        short check: the process is considered started once /health answers 200.

        Proxy stdout/stderr are captured to <data_dir>/proxy.log so startup
        failures can actually be diagnosed.

        Args:
            timeout: Maximum seconds to wait for the proxy to become healthy.

        Returns:
            True if started successfully, False otherwise
        """
        with self._lock:
            if self.is_running():
                log.warning(f"mnemonic-proxy for {self.model_name} already running on port {self.proxy_port}")
                return True

            if self._starting:
                log.warning(f"mnemonic-proxy for {self.model_name} is already starting")
                return False

            try:
                cmd = self.build_cmd()

                # Prepare environment
                env = os.environ.copy()
                # Add Intel oneAPI env if needed (same as processes.py)
                from webapp.processes import _get_intel_env
                env.update(_get_intel_env())

                # Resolve data directory and ensure it exists
                data_dir = self._resolve_data_dir()
                data_dir.mkdir(parents=True, exist_ok=True)

                # Clean up stale lock files that might prevent proxy startup
                # The mnemonic-proxy binary creates a lock file in kv-archive/.lock
                # to prevent concurrent access. If a previous proxy didn't shut down
                # cleanly, this lock file can cause "data directory is in use" errors.
                kv_archive_lock = data_dir / "kv-archive" / ".lock"
                if kv_archive_lock.exists():
                    try:
                        kv_archive_lock.unlink()
                        log.info(f"Removed stale kv-archive lock file for {self.model_name}: {kv_archive_lock}")
                    except Exception as e:
                        log.warning(f"Failed to remove stale kv-archive lock file for {self.model_name}: {e}")

                # Capture stdout/stderr - DEVNULL here is why startup failures
                # used to be undiagnosable.
                self._log_handle = open(self.log_path, "a", encoding="utf-8")
                self._log_handle.write(
                    f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start: {' '.join(cmd)}\n"
                )
                self._log_handle.flush()

                # Start process (build_cmd already writes config and passes --data/--config)
                self._process = subprocess.Popen(
                    cmd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,  # Create new process group for clean kill
                )

                self._pid = self._process.pid
                self._start_time = time.time()
                self._starting = True

                log.info(
                    f"Started mnemonic-proxy for {self.model_name} (pid={self._pid}) "
                    f"on port {self.proxy_port} (log: {self.log_path})"
                )

            except Exception as e:
                log.error(f"Failed to start mnemonic-proxy for {self.model_name}: {e}")
                self._cleanup()
                return False

        # Poll for readiness OUTSIDE the lock so stop() is never blocked by a
        # slow startup.
        deadline = time.time() + max(timeout, 0.0)
        # Quick check: verify we can connect to the port (process actually bound it)
        # This catches "Address already in use" immediately
        port_bound = False
        for _ in range(10):  # Quick retries
            if self._process is not None and self._process.poll() is not None:
                log.error(
                    f"mnemonic-proxy for {self.model_name} exited during startup "
                    f"(code {self._process.returncode})"
                )
                self._log_tail()
                self._kill_process(self._pid)
                self._cleanup()
                return False
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                result = s.connect_ex(('127.0.0.1', self.proxy_port))
                s.close()
                if result == 0:
                    port_bound = True
                    break
            except Exception:
                pass
            time.sleep(0.2)
        
        if not port_bound:
            log.error(
                f"mnemonic-proxy for {self.model_name} failed to bind port "
                f"{self.proxy_port} (Address already in use or process exited)"
            )
            self._log_tail()
            if self._pid:
                self._kill_process(self._pid)
            self._cleanup()
            return False

        try:
            while True:
                # Process died during startup?
                if self._process is not None and self._process.poll() is not None:
                    log.error(
                        f"mnemonic-proxy for {self.model_name} exited during startup "
                        f"(code {self._process.returncode})"
                    )
                    self._log_tail()
                    self._kill_process(self._pid)
                    self._cleanup()
                    return False

                if self._health_state(timeout=PROXY_START_HEALTH_TIMEOUT) == "ok":
                    log.info(
                        f"mnemonic-proxy for {self.model_name} healthy on port "
                        f"{self.proxy_port} after {time.time() - self._start_time:.1f}s"
                    )
                    return True

                if time.time() >= deadline:
                    break
                time.sleep(PROXY_START_POLL_INTERVAL)

            # Timed out. Never leave the process orphaned holding the port.
            log.error(
                f"mnemonic-proxy for {self.model_name} failed to become healthy on port "
                f"{self.proxy_port} within {timeout:.0f}s (last health: {self._last_health})"
            )
            self._log_tail()
            if self._pid:
                self._kill_process(self._pid)
            self._cleanup()
            return False
        finally:
            with self._lock:
                self._starting = False
    
    def stop(self) -> bool:
        """
        Stop the mnemonic-proxy process gracefully.
        
        Returns:
            True if stopped successfully, False otherwise
        """
        with self._lock:
            if not self._pid:
                # Still try to stop MCP if running
                if self.mcp_enabled and self._mcp_pid:
                    self.stop_mcp()
                return True
            
            pid = self._pid
            success = self._kill_process(pid)
            
            self._cleanup()
            
            # Also stop MCP server if running
            if self.mcp_enabled and self._mcp_pid:
                self.stop_mcp()
            
            log.info(f"Stopped mnemonic-proxy for {self.model_name} (pid={pid}): success={success}")
            return success
    
    def _kill_process(self, pid: int) -> bool:
        """Kill a process and its children."""
        try:
            if WINDOWS or (WSL and False):  # Windows-style kill
                # Use taskkill on Windows
                cmd_path = "/mnt/c/Windows/System32/cmd.exe" if WSL else "cmd.exe"
                if os.path.exists(cmd_path):
                    result = subprocess.run(
                        [cmd_path, "/c", f"taskkill /F /T /PID {pid} 2>&1"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    return "successful" in result.stdout.lower() or result.returncode == 0
            else:
                # Unix-style: kill process group
                try:
                    os.killpg(pid, signal.SIGTERM)
                    time.sleep(0.5)
                    try:
                        os.killpg(pid, 0)
                        os.killpg(pid, signal.SIGKILL)
                    except OSError:
                        pass
                    return True
                except (OSError, ProcessLookupError):
                    try:
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(0.5)
                        try:
                            os.kill(pid, 0)
                            os.kill(pid, signal.SIGKILL)
                        except OSError:
                            pass
                        return True
                    except (OSError, ProcessLookupError):
                        return True
        except Exception as e:
            log.error(f"Failed to kill mnemonic-proxy process {pid}: {e}")
            return False
        
        return False
    
    def _cleanup(self) -> None:
        """Clean up process references and reap child process."""
        # Reap the child process to prevent zombies
        if self._process:
            try:
                # Try to wait with a short timeout
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                # Process still alive after kill, force reap
                try:
                    self._process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass  # Give up, process will be reaped by init
            except Exception:
                pass  # Ignore any other errors during reap
        
        # Clean up stale lock files in data directory
        # This helps prevent "data directory is in use" errors on next start
        try:
            data_dir = self._resolve_data_dir()
            kv_archive_lock = data_dir / "kv-archive" / ".lock"
            if kv_archive_lock.exists():
                kv_archive_lock.unlink()
                log.debug(f"Cleaned up kv-archive lock file for {self.model_name}: {kv_archive_lock}")
        except Exception:
            pass  # Ignore cleanup errors
        
        self._pid = None
        self._process = None
        self._start_time = None
        self._last_health = None
        # Release the log file handle (the child already exited)
        if self._log_handle:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None

    def _process_alive(self) -> bool:
        """True if the tracked proxy process still exists."""
        if not self._pid:
            return False
        
        # First check if we have a subprocess.Popen object and it's still running
        if self._process is not None:
            if self._process.poll() is None:
                return True
            # Process has exited
            return False
        
        # Fallback: check if PID exists (for cases where _process is not tracked)
        try:
            if WINDOWS or (WSL and False):
                cmd_path = "/mnt/c/Windows/System32/cmd.exe" if WSL else "cmd.exe"
                if os.path.exists(cmd_path):
                    result = subprocess.run(
                        [cmd_path, "/c", f"tasklist /FI \"PID eq {self._pid}\" 2>&1"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    return str(self._pid) in result.stdout
                return False
            os.kill(self._pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _health_state(self, timeout: float = PROXY_HEALTH_TIMEOUT) -> str:
        """
        Query the proxy's /health endpoint and classify the result.

        Returns:
            "ok"        - /health answered 200 (proxy + engine healthy)
            "listening" - the proxy answered but not with 200 (engine still
                          loading/unreachable), or the connection was accepted
                          but the handler was too slow to answer in time
            "down"      - nothing is listening on the proxy port (still starting
                          up, or the process died)
        """
        if not self._pid:
            self._last_health = "down"
            return "down"
        
        # Check if process is still alive first
        if self._process is not None and self._process.poll() is not None:
            self._last_health = "down"
            return "down"
        
        # Use the correct bind address for health check
        # If bind_host is 0.0.0.0, we can check via 127.0.0.1 (loopback works)
        # But we need to ensure we're hitting OUR process, not a stale one
        try:
            resp = requests.get(
                f"http://127.0.0.1:{self.proxy_port}/health",
                timeout=timeout,
            )
            if resp.status_code == 200:
                # Additional verification: check the response contains expected proxy info
                try:
                    health_data = resp.json()
                    # Verify this is our proxy by checking version or model info
                    # If it responds with a valid health structure, assume it's ours
                    state = "ok"
                except Exception:
                    state = "listening"
            else:
                # 503 = engine still loading, other codes = engine error:
                # the proxy itself is up and serving.
                state = "listening"
            self._last_health = state
            return state
        except requests.exceptions.Timeout:
            # TCP connected but no answer within the timeout: the proxy is
            # listening, its handler is busy (typically proxying /health to a
            # slow upstream engine).
            self._last_health = "listening"
            return "listening"
        except requests.exceptions.ConnectionError:
            self._last_health = "down"
            return "down"
        except Exception:
            self._last_health = "down"
            return "down"

    def _log_tail(self, lines: int = 30) -> list[str]:
        """Read the tail of the proxy log file (for diagnostics)."""
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                tail = f.read().splitlines()[-lines:]
        except OSError:
            return []
        for line in tail:
            log.error(f"mnemonic-proxy[{self.model_name}] log: {line}")
        return tail
    
    def is_running(self) -> bool:
        """
        Check if the mnemonic-proxy process is running.

        Returns:
            True if proxy process is alive and port is bound (health state "ok" or "listening"),
            False if process died or port not bound ("down").
        """
        if not self._pid:
            return False

        # Check if process exists
        if not self._process_alive():
            return False

        # Check health endpoint - proxy is "running" if process alive and port bound
        # "ok" = proxy + engine healthy, "listening" = proxy up but engine not ready
        health = self._health_state()
        return health in ("ok", "listening")

    def _check_health(self) -> bool:
        """Check mnemonic-proxy health via its health endpoint.
        
        Returns True if proxy process is alive and port is bound ("ok" or "listening").
        """
        return self._health_state() in ("ok", "listening")
    
    def get_status(self) -> dict[str, Any]:
        """
        Get detailed status of the mnemonic-proxy.
        
        Returns:
            Dictionary with status information
        """
        running = self.is_running()
        
        status = {
            "model_name": self.model_name,
            "llama_port": self.llama_port,
            "proxy_port": self.proxy_port,
            "running": running,
            "pid": self._pid,
            "uptime_seconds": time.time() - self._start_time if self._start_time and running else 0,
            "data_dir": self.data_dir,
            "slot_dir": self.slot_dir,
            "mcp_enabled": self.mcp_enabled,
            "mcp_port": self.mcp_port if self.mcp_enabled else None,
        }
        
        # Include MCP status
        status["mcp"] = self.get_mcp_status()
        
        if running:
            # Try to get recent proxy events (v0.3.24 has no /stats endpoint;
            # /v1/strata/journal returns the recent event list)
            try:
                resp = requests.get(
                    f"http://127.0.0.1:{self.proxy_port}/v1/strata/journal",
                    timeout=2.0
                )
                if resp.status_code == 200:
                    events = resp.json().get("events", [])
                    status["recent_events"] = len(events)
                    status["last_event"] = events[-1] if events else None
            except Exception:
                pass

        return status

    def get_logs(self, lines: int = 100) -> list[str]:
        """
        Get recent log lines from the mnemonic-proxy process.

        Proxy stdout/stderr are captured to <data_dir>/proxy.log by start(),
        so this reads the tail of that file.

        Args:
            lines: Number of lines to retrieve

        Returns:
            List of log lines
        """
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read().splitlines()[-lines:]
        except OSError:
            return []
    
    # ==================== MCP Server Management ====================
    
    def _get_mcp_binary(self) -> Optional[Path]:
        """
        Find the mnemonic-mcp binary/executable.
        
        Returns:
            Path to mnemonic-mcp executable or None if not found
        """
        candidates = [
            Path(shutil.which("mnemonic-mcp")) if shutil.which("mnemonic-mcp") else None,
            BASE_DIR / "mnemonic-mcp",
            BASE_DIR / "mnemonic-mcp.exe",
            Path.home() / ".local" / "bin" / "mnemonic-mcp",
            Path("/usr/local/bin/mnemonic-mcp"),
        ]
        
        for candidate in candidates:
            if candidate and candidate.exists():
                return candidate
        
        # Try to run via python -m
        try:
            result = subprocess.run(
                ["python3", "-m", "mnemonic_proxy.mcp", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return Path("python3")  # Special marker for module execution
        except Exception:
            pass
        
        return None
    
    def _build_mcp_cmd(self) -> list[str]:
        """
        Build the command for starting mnemonic-mcp server.
        
        Returns:
            List of command arguments
        """
        binary = self._get_mcp_binary()
        if not binary:
            raise RuntimeError("mnemonic-mcp not found. Install with: pip install mnemonic-proxy")
        
        # Archive database path (in data_dir)
        archive_db = self._resolve_data_dir() / "archive.sqlite"
        
        cmd: list[str] = []
        
        if binary.name == "python3":
            # Run as module
            cmd = ["python3", "-m", "mnemonic_proxy.mcp"]
        else:
            cmd = [str(binary)]
        
        # MCP server settings
        cmd += [
            "--db", str(archive_db),
            "--http", str(self.mcp_port),
            "--host", "0.0.0.0",
        ]
        
        log.info(f"Built mnemonic-mcp command for {self.model_name}: {cmd}")
        return cmd
    
    def start_mcp(self) -> bool:
        """
        Start the mnemonic-mcp server process.
        
        Returns:
            True if started successfully, False otherwise
        """
        if not self.mcp_enabled:
            log.warning(f"MCP server not enabled for {self.model_name}")
            return False
        
        with self._mcp_lock:
            if self.is_mcp_running():
                log.warning(f"mnemonic-mcp for {self.model_name} already running on port {self.mcp_port}")
                return True
            
            try:
                cmd = self._build_mcp_cmd()
                
                # Prepare environment
                env = os.environ.copy()
                from webapp.processes import _get_intel_env
                env.update(_get_intel_env())
                
                # Capture output to the data dir for diagnostics
                mcp_log_path = self._resolve_data_dir() / "mnemonic-mcp.log"
                self._mcp_log_handle = open(mcp_log_path, "a", encoding="utf-8")
                self._mcp_log_handle.write(
                    f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start: {' '.join(cmd)}\n"
                )
                self._mcp_log_handle.flush()
                
                # Start process
                self._mcp_process = subprocess.Popen(
                    cmd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=self._mcp_log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                
                self._mcp_pid = self._mcp_process.pid
                self._mcp_start_time = time.time()
                
                log.info(
                    f"Started mnemonic-mcp for {self.model_name} (pid={self._mcp_pid}) "
                    f"on port {self.mcp_port} (log: {mcp_log_path})"
                )
                
            except Exception as e:
                log.error(f"Failed to start mnemonic-mcp for {self.model_name}: {e}")
                self._cleanup_mcp()
                return False
        
        # Poll for readiness outside the lock (same rationale as start())
        deadline = time.time() + MCP_START_TIMEOUT
        while True:
            if self._mcp_process is not None and self._mcp_process.poll() is not None:
                log.error(
                    f"mnemonic-mcp for {self.model_name} exited during startup "
                    f"(code {self._mcp_process.returncode})"
                )
                self._kill_process(self._mcp_pid)
                self._cleanup_mcp()
                return False
            
            if self.is_mcp_running():
                log.info(f"mnemonic-mcp for {self.model_name} healthy on port {self.mcp_port}")
                return True
            
            if time.time() >= deadline:
                break
            time.sleep(PROXY_START_POLL_INTERVAL)
        
        log.error(f"mnemonic-mcp for {self.model_name} failed to start on port {self.mcp_port}")
        if self._mcp_pid:
            self._kill_process(self._mcp_pid)
        self._cleanup_mcp()
        return False
    
    def stop_mcp(self) -> bool:
        """
        Stop the mnemonic-mcp server process gracefully.
        
        Returns:
            True if stopped successfully, False otherwise
        """
        with self._mcp_lock:
            if not self._mcp_pid:
                return True
            
            pid = self._mcp_pid
            success = self._kill_process(pid)
            
            self._cleanup_mcp()
            
            log.info(f"Stopped mnemonic-mcp for {self.model_name} (pid={pid}): success={success}")
            return success
    
    def _cleanup_mcp(self) -> None:
        """Clean up MCP process references and reap child process."""
        # Reap the child process to prevent zombies
        if self._mcp_process:
            try:
                # Try to wait with a short timeout
                self._mcp_process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                # Process still alive after kill, force reap
                try:
                    self._mcp_process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass  # Give up, process will be reaped by init
            except Exception:
                pass  # Ignore any other errors during reap
        self._mcp_pid = None
        self._mcp_process = None
        self._mcp_start_time = None
        if self._mcp_log_handle:
            try:
                self._mcp_log_handle.close()
            except Exception:
                pass
            self._mcp_log_handle = None
    
    def is_mcp_running(self) -> bool:
        """
        Check if the mnemonic-mcp server is running and responsive.
        
        Returns:
            True if running and healthy, False otherwise
        """
        if not self._mcp_pid:
            return False
        
        # Check if process exists
        try:
            if WINDOWS or (WSL and False):
                cmd_path = "/mnt/c/Windows/System32/cmd.exe" if WSL else "cmd.exe"
                if os.path.exists(cmd_path):
                    result = subprocess.run(
                        [cmd_path, "/c", f"tasklist /FI \"PID eq {self._mcp_pid}\" 2>&1"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if str(self._mcp_pid) not in result.stdout:
                        return False
            else:
                os.kill(self._mcp_pid, 0)
        except (OSError, ProcessLookupError):
            return False
        
        # Check health endpoint
        return self._check_mcp_health()
    
    def _check_mcp_health(self) -> bool:
        """Check mnemonic-mcp health via MCP initialize request."""
        try:
            # MCP health check - send initialize request
            resp = requests.post(
                f"http://127.0.0.1:{self.mcp_port}/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "health-check", "version": "1.0"}
                    }
                },
                timeout=2.0
            )
            return resp.status_code == 200
        except Exception:
            return False
    
    def get_mcp_status(self) -> dict[str, Any]:
        """
        Get detailed status of the mnemonic-mcp server.
        
        Returns:
            Dictionary with MCP status information
        """
        running = self.is_mcp_running()
        
        status = {
            "enabled": self.mcp_enabled,
            "running": running,
            "port": self.mcp_port if self.mcp_enabled else None,
            "pid": self._mcp_pid if running else None,
            "uptime_seconds": time.time() - self._mcp_start_time if self._mcp_start_time and running else 0,
        }
        
        return status


# Global proxy manager instances (one per model)
_proxy_managers: dict[str, MnemonicProxyManager] = {}
_proxy_managers_lock = threading.Lock()


def get_proxy_manager(model_name: str) -> Optional[MnemonicProxyManager]:
    """Get the proxy manager for a model."""
    with _proxy_managers_lock:
        return _proxy_managers.get(model_name)


def create_proxy_manager(
    model_name: str,
    llama_port: int,
    proxy_port: int = 8096,
    data_dir: str = "",
    slot_dir: str = "",
    tokenizer_path: str = "",
    config_override: Optional[dict] = None,
    mcp_enabled: bool = False,
    mcp_port: int = 8133,
) -> MnemonicProxyManager:
    """Create or get a proxy manager for a model.

    A manager that is not running is replaced with a fresh one so that changed
    model settings (port, data dir, config) take effect; a running manager is
    kept as-is (its process holds the port).
    """
    with _proxy_managers_lock:
        existing = _proxy_managers.get(model_name)
        if existing is not None:
            if existing._pid:
                log.warning(
                    f"mnemonic-proxy for {model_name} is already running "
                    f"(pid={existing._pid}); keeping the existing manager"
                )
                return existing
            # Not running: drop the stale instance so new config is applied
            _proxy_managers.pop(model_name, None)
        _proxy_managers[model_name] = MnemonicProxyManager(
            model_name=model_name,
            llama_port=llama_port,
            proxy_port=proxy_port,
            data_dir=data_dir,
            slot_dir=slot_dir,
            tokenizer_path=tokenizer_path,
            config_override=config_override,
            mcp_enabled=mcp_enabled,
            mcp_port=mcp_port,
        )
        return _proxy_managers[model_name]


def remove_proxy_manager(model_name: str) -> bool:
    """Remove and stop a proxy manager."""
    with _proxy_managers_lock:
        manager = _proxy_managers.pop(model_name, None)
        if manager:
            manager.stop()
            return True
        return False


def list_proxy_managers() -> list[dict[str, Any]]:
    """List all proxy managers with their status."""
    with _proxy_managers_lock:
        return [m.get_status() for m in _proxy_managers.values()]


def stop_all_proxies() -> dict[str, bool]:
    """Stop all running proxy managers."""
    with _proxy_managers_lock:
        results = {}
        for name, manager in _proxy_managers.items():
            results[name] = manager.stop()
        _proxy_managers.clear()
        return results


def regenerate_all_proxy_configs() -> dict[str, Path]:
    """
    Regenerate mnemonic-proxy config.json files for all known models.
    
    This is useful when the generate_config() logic has changed and existing
    config.json files on disk are stale. It creates proxy managers for all
    models in models.json and writes fresh config files.
    
    Returns:
        Dictionary mapping model names to their config file paths
    """
    from webapp.models import get_models
    
    models = get_models()
    results = {}
    
    for model_name, model_config in models.items():
        try:
            llama_port = model_config.get("port")
            if not llama_port:
                log.warning(f"Skipping {model_name}: no port configured")
                continue
            
            # Extract mnemonic-proxy settings from model config
            mp_config = model_config.get("mnemonic_proxy", {})
            
            manager = MnemonicProxyManager(
                model_name=model_name,
                llama_port=llama_port,
                proxy_port=mp_config.get("port", 8096),
                data_dir=mp_config.get("data_dir", ""),
                slot_dir=mp_config.get("slot_dir", ""),
                tokenizer_path=mp_config.get("tokenizer_path", ""),
                config_override=mp_config.get("config_override", {}),
                mcp_enabled=mp_config.get("mcp_enabled", False),
                mcp_port=mp_config.get("mcp_port", 8133),
            )
            
            config_path = manager._write_config_file()
            results[model_name] = config_path
            log.info(f"Regenerated mnemonic-proxy config for {model_name}: {config_path}")
            
        except Exception as e:
            log.error(f"Failed to regenerate config for {model_name}: {e}")
    
    return results


import shutil