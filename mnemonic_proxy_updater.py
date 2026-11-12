#!/usr/bin/env python3
"""
Mnemonic Proxy Updater

Updates mnemonic-proxy Python package via pip.
Fetches the latest version from PyPI/GitHub and upgrades the package.

The module:
1. Fetches the latest version from GitHub releases or PyPI
2. Upgrades the package via pip
3. Provides progress/status callbacks
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Callable, Optional, Dict, List

from config import BASE_DIR

log = logging.getLogger("llamaweb")


# ──────────────────────────────────────────────────────────────────────────────
# GitHub API helpers
# ──────────────────────────────────────────────────────────────────────────────

GITHUB_API_URL = "https://api.github.com/repos/lastreload/mnemonic-proxy/releases"
PYPI_API_URL = "https://pypi.org/pypi/mnemonic-proxy/json"


def _github_request(url: str, timeout: int = 30) -> dict[str, Any] | None:
    """Make a request to the GitHub API with proper headers."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "llama.cpp-model-manager",
            "Accept": "application/vnd.github.v3+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        log.warning(f"GitHub API request failed: {e}")
        return None


def _pypi_request(url: str, timeout: int = 30) -> dict[str, Any] | None:
    """Make a request to the PyPI API."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "llama.cpp-model-manager",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        log.warning(f"PyPI API request failed: {e}")
        return None


def _get_latest_github_release() -> Optional[str]:
    """Get the latest release tag from GitHub releases."""
    data = _github_request(f"{GITHUB_API_URL}?per_page=10")
    if not data:
        return None
    
    # Filter for non-draft, non-prerelease releases
    for release in data:
        if not release.get("draft", False) and not release.get("prerelease", False):
            tag = release.get("tag_name", "")
            if tag:
                return tag
    
    return None


def _get_latest_pypi_version() -> Optional[str]:
    """Get the latest version from PyPI."""
    data = _pypi_request(PYPI_API_URL)
    if not data or "info" not in data:
        return None
    
    version = data["info"].get("version")
    if version:
        return f"v{version}"  # Normalize to vX.Y.Z format
    
    return None


def _get_current_version() -> Optional[str]:
    """Get the currently installed mnemonic-proxy version."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", "mnemonic-proxy"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Version: "):
                    version = line.split("Version: ")[1].strip()
                    return f"v{version}"
    except Exception as e:
        log.warning(f"Failed to get current mnemonic-proxy version: {e}")
    return None


def _run_pip_install(package: str, progress_callback: Callable[[str, int], None]) -> Dict[str, Any]:
    """Run pip install --upgrade for a package."""
    try:
        progress_callback("Starting pip upgrade...", 10)
        
        # Use pip install --upgrade
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", package]
        
        progress_callback("Running pip install...", 30)
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        
        progress_callback("Processing output...", 80)
        
        if result.returncode == 0:
            # Get the new version
            new_version = _get_current_version()
            return {
                "success": True,
                "version": new_version,
                "output": result.stdout,
            }
        else:
            return {
                "success": False,
                "errors": [result.stderr or result.stdout or "Unknown error"],
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "errors": ["pip install timed out after 5 minutes"]}
    except Exception as e:
        log.exception("pip install failed")
        return {"success": False, "errors": [str(e)]}


def update_mnemonic_proxy(
    progress_callback: Callable[[str, int], None],
    target_version: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update mnemonic-proxy to the latest version.
    
    Args:
        progress_callback: Function(message, percent) called with progress updates
        target_version: Optional specific version to install (e.g., "v0.3.26")
    
    Returns:
        Dict with success status, version, and any errors
    """
    progress_callback("Checking current version...", 5)
    
    current_version = _get_current_version()
    log.info(f"Current mnemonic-proxy version: {current_version}")
    
    if target_version:
        # User specified a version
        progress_callback(f"Installing version {target_version}...", 20)
        package_spec = f"mnemonic-proxy=={target_version.lstrip('v')}"
        result = _run_pip_install(package_spec, progress_callback)
        if result.get("success"):
            result["tag"] = target_version
        return result
    
    # Check for latest version
    progress_callback("Fetching latest version from GitHub...", 15)
    
    latest_github = _get_latest_github_release()
    latest_pypi = _get_latest_pypi_version()
    
    # Prefer GitHub release tag, fallback to PyPI
    latest_version = latest_github or latest_pypi
    
    if not latest_version:
        return {"success": False, "errors": ["Failed to fetch latest version from GitHub or PyPI"]}
    
    log.info(f"Latest mnemonic-proxy version: {latest_version}")
    progress_callback(f"Latest version: {latest_version}", 20)
    
    if current_version and current_version == latest_version:
        return {
            "success": True,
            "version": current_version,
            "tag": latest_version,
            "message": f"Already at latest version ({current_version})",
        }
    
    progress_callback(f"Upgrading from {current_version or 'unknown'} to {latest_version}...", 25)
    
    # Run pip upgrade
    result = _run_pip_install("mnemonic-proxy", progress_callback)
    
    if result.get("success"):
        result["tag"] = latest_version
        result["previous_version"] = current_version
    
    return result