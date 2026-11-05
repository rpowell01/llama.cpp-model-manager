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
import threading
import time
from pathlib import Path
from typing import Any, Optional

import requests

from config import (
    BASE_DIR,
    DEFAULTS,
    WINDOWS,
    WSL,
    convert_path_for_binary,
    resolve_model_path,
)
from models import InvalidModeError, get_models

log = logging.getLogger("llamaweb.mnemonic_proxy")


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
        self.config_override = config_override or {
            "mask_anchor": True,
            "autosave": True,
            "window_estimate_slack": 0.15
        }
        self.mcp_enabled = mcp_enabled
        self.mcp_port = mcp_port
        
        # Process tracking
        self._pid: Optional[int] = None
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._start_time: Optional[float] = None
        
        # MCP server tracking
        self._mcp_pid: Optional[int] = None
        self._mcp_process: Optional[subprocess.Popen] = None
        self._mcp_lock = threading.Lock()
        self._mcp_start_time: Optional[float] = None
        
        # Ensure data directory exists
        self._ensure_data_dir()
    
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
        
        # Write fresh config file so proxy uses the verified n_ctx / window size
        self._write_config_file()
        config_path = data_dir / "config.json"
        
        cmd = [str(binary)]
        
        # Core proxy settings - pass data dir and config explicitly
        cmd += [
            "--port", str(self.proxy_port),
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
        
        # Get slot directory from model's slot_save_path (required for saved state)
        slot_save_path = model_config.get("slot_save_path", "")
        slot_dir = self.slot_dir or slot_save_path
        
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
        # Only write what the proxy cannot discover
        config = {
            "engine": engine,
            "upstream": f"http://127.0.0.1:{self.llama_port}",
        }
        
        # Add slot_dir if available (required for saved state to work)
        if slot_dir:
            config["slot_dir"] = slot_dir
        
        # Add tokenizer if available (for accurate token counting)
        if tokenizer:
            config["tokenizer"] = tokenizer
        
        # Merge config_override (allows overriding any setting including window)
        if self.config_override:
            config.update(self.config_override)
        
        # MCP server config
        if self.mcp_enabled:
            config["mcp_port"] = self.mcp_port
        
        log.info(f"Generated mnemonic-proxy config for {self.model_name}: {config}")
        return config
    
    def _write_config_file(self) -> Path:
        """
        Write the generated config to a JSON file in the data directory.
        
        Returns:
            Path to the written config file
        """
        config = self.generate_config()
        config_path = self._resolve_data_dir() / "config.json"
        
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        
        log.debug(f"Wrote mnemonic-proxy config to {config_path}")
        return config_path
    
    def start(self) -> bool:
        """
        Start the mnemonic-proxy process.
        
        Returns:
            True if started successfully, False otherwise
        """
        with self._lock:
            if self.is_running():
                log.warning(f"mnemonic-proxy for {self.model_name} already running on port {self.proxy_port}")
                return True
            
            try:
                cmd = self.build_cmd()
                
                # Prepare environment
                env = os.environ.copy()
                # Add Intel oneAPI env if needed (same as processes.py)
                from processes import _get_intel_env
                env.update(_get_intel_env())
                
                # Resolve data directory and ensure it exists
                data_dir = self._resolve_data_dir()
                data_dir.mkdir(parents=True, exist_ok=True)
                
                # Start process (build_cmd already writes config and passes --data/--config)
                self._process = subprocess.Popen(
                    cmd,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,  # Create new process group for clean kill
                )
                
                self._pid = self._process.pid
                self._start_time = time.time()
                
                log.info(f"Started mnemonic-proxy for {self.model_name} (pid={self._pid}) on port {self.proxy_port}")
                
                # Wait a moment and verify it's up (proxy may need 2-3 seconds to fully start)
                time.sleep(3.0)
                if self.is_running():
                    return True
                else:
                    log.error(f"mnemonic-proxy for {self.model_name} failed to start")
                    self._cleanup()
                    return False
                    
            except Exception as e:
                log.error(f"Failed to start mnemonic-proxy for {self.model_name}: {e}")
                self._cleanup()
                return False
    
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
            mcp_stopped = False
            if self.mcp_enabled and self._mcp_pid:
                mcp_stopped = self.stop_mcp()
                if mcp_stopped:
                    log.info(f"Stopped mnemonic-mcp for {self.model_name}")
            
            log.info(f"Stopped mnemonic-proxy for {self.model_name} (pid={pid}): success={success}, mcp_stopped={mcp_stopped}")
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
        self._pid = None
        self._process = None
        self._start_time = None
    
    def is_running(self) -> bool:
        """
        Check if the mnemonic-proxy is running and responsive.
        
        Returns:
            True if running and healthy, False otherwise
        """
        if not self._pid:
            return False
        
        # Check if process exists
        try:
            if WINDOWS or (WSL and False):
                # On Windows, use tasklist
                cmd_path = "/mnt/c/Windows/System32/cmd.exe" if WSL else "cmd.exe"
                if os.path.exists(cmd_path):
                    result = subprocess.run(
                        [cmd_path, "/c", f"tasklist /FI \"PID eq {self._pid}\" 2>&1"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if str(self._pid) not in result.stdout:
                        return False
            else:
                # Unix: send signal 0 to check existence
                os.kill(self._pid, 0)
        except (OSError, ProcessLookupError):
            return False
        
        # Check health endpoint
        return self._check_health()
    
    def _check_health(self) -> bool:
        """Check mnemonic-proxy health via its health endpoint."""
        try:
            resp = requests.get(
                f"http://127.0.0.1:{self.proxy_port}/health",
                timeout=2.0
            )
            return resp.status_code == 200
        except Exception:
            return False
    
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
            # Try to get proxy stats
            try:
                resp = requests.get(
                    f"http://127.0.0.1:{self.proxy_port}/stats",
                    timeout=2.0
                )
                if resp.status_code == 200:
                    status["stats"] = resp.json()
            except Exception:
                pass
        
        return status
    
    def get_logs(self, lines: int = 100) -> list[str]:
        """
        Get recent log lines from the mnemonic-proxy process.
        
        Args:
            lines: Number of lines to retrieve
            
        Returns:
            List of log lines
        """
        if not self._process:
            return []
        
        # Note: In production, you'd want to use a proper log capture mechanism
        # This is a simplified version - the process stdout/stderr pipes would need
        # to be read in a background thread for real-time log streaming
        return ["Log streaming not yet implemented - check process stdout/stderr"]
    
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
                from processes import _get_intel_env
                env.update(_get_intel_env())
                
                # Start process
                self._mcp_process = subprocess.Popen(
                    cmd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                
                self._mcp_pid = self._mcp_process.pid
                self._mcp_start_time = time.time()
                
                log.info(f"Started mnemonic-mcp for {self.model_name} (pid={self._mcp_pid}) on port {self.mcp_port}")
                
                # Wait a moment and verify it's up
                time.sleep(1.0)
                if self.is_mcp_running():
                    return True
                else:
                    log.error(f"mnemonic-mcp for {self.model_name} failed to start")
                    self._cleanup_mcp()
                    return False
                    
            except Exception as e:
                log.error(f"Failed to start mnemonic-mcp for {self.model_name}: {e}")
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
    """Create or get a proxy manager for a model."""
    with _proxy_managers_lock:
        if model_name not in _proxy_managers:
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
    from models import get_models
    
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