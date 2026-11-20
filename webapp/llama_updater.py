#!/usr/bin/env python3
"""
Llama.cpp Backend Updater

Downloads and updates llama.cpp binaries for Windows and Linux platforms.
Fetches the latest nightly build tag from GitHub and installs binaries
for all platform/backend combinations.

Supported platforms: windows, linux
Supported backends: vulkan, sycl, openvino

The module:
1. Fetches the latest nightly build tag from GitHub API
2. Downloads appropriate binaries for each platform/backend combination
3. Extracts them to the appropriate directories
4. Updates the .llama-platform-config.json with the new paths
5. Provides progress/status callbacks
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Callable, Optional, Dict, List, Tuple

from webapp.config import BASE_DIR

log = logging.getLogger("llamaweb")


# ──────────────────────────────────────────────────────────────────────────────
# GitHub API helpers
# ──────────────────────────────────────────────────────────────────────────────

GITHUB_API_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases"


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


def _get_latest_nightly_tag() -> Optional[str]:
    """Get the latest nightly build tag from GitHub releases.
    
    Nightly builds are tagged with 'b' followed by numbers (e.g., b11928, b12189).
    Returns the tag_name of the most recent one.
    """
    data = _github_request(f"{GITHUB_API_URL}?per_page=100")
    if not data:
        return None
    
    # Filter for nightly builds (tags starting with 'b')
    nightly_tags = []
    for release in data:
        tag = release.get("tag_name", "")
        # Nightly tags start with 'b' followed by numbers
        if re.match(r"^b\d+$", tag):
            # Extract the numeric part
            match = re.match(r"^b(\d+)$", tag)
            if match:
                nightly_tags.append((int(match.group(1)), tag, release.get("published_at", "")))
    
    if not nightly_tags:
        return None
    
    # Sort by the numeric part descending, return the most recent
    nightly_tags.sort(key=lambda x: x[0], reverse=True)
    return nightly_tags[0][1]


def _get_release_assets(tag: str) -> dict[str, dict[str, str]]:
    """Get all assets for a specific release tag.
    
    Returns a dict mapping asset name -> {name, browser_download_url, size}
    """
    url = f"{GITHUB_API_URL}/tags/{tag}"
    data = _github_request(url)
    if not data or "assets" not in data:
        return {}
    
    assets = {}
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        assets[name] = {
            "name": name,
            "url": asset.get("browser_download_url", ""),
            "size": asset.get("size", 0),
        }
    return assets


# ──────────────────────────────────────────────────────────────────────────────
# Platform/backend directory configuration
# ──────────────────────────────────────────────────────────────────────────────

# Default platform/backend combinations (6 total, plus global_default)
PLATFORM_BACKEND_COMBINATIONS = [
    "windows-vulkan",
    "windows-sycl",
    "windows-openvino",
    "linux-vulkan",
    "linux-sycl",
    "linux-openvino",
]


def _get_config_file() -> Path:
    """Get the path to the platform/backend config file."""
    return BASE_DIR / "webapp" / "data" / ".llama-platform-config.json"


def _load_config() -> dict[str, str]:
    """Load the platform/backend directory configuration."""
    config_file = _get_config_file()
    config = {}
    
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"Failed to load platform/backend config: {e}")
    
    # Ensure all combinations exist
    for combo in PLATFORM_BACKEND_COMBINATIONS:
        if combo not in config:
            config[combo] = ""
    
    if "global_default" not in config:
        config["global_default"] = ""
    
    return config


def _save_config(config: dict[str, str]) -> None:
    """Save the platform/backend directory configuration."""
    config_file = _get_config_file()
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        log.info(f"Saved platform/backend config to {config_file}")
    except IOError as e:
        log.error(f"Failed to save platform/backend config: {e}")


def _get_platform_backend_dir(platform: str, backend: str) -> str:
    """Get the llama-server directory for a platform/backend combination."""
    config = _load_config()
    key = f"{platform}-{backend}"
    return config.get(key, "")


def _set_platform_backend_dir(platform: str, backend: str, directory: str) -> None:
    """Set the llama-server directory for a platform/backend combination."""
    config = _load_config()
    key = f"{platform}-{backend}"
    config[key] = directory
    _save_config(config)


# ──────────────────────────────────────────────────────────────────────────────
# Binary download and extraction
# ──────────────────────────────────────────────────────────────────────────────

def _extract_tar_gz(archive_path: Path, dest_dir: Path) -> bool:
    """Extract a .tar.gz archive to the destination directory."""
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(dest_dir)
        return True
    except (tarfile.TarError, IOError, OSError) as e:
        log.error(f"Failed to extract {archive_path}: {e}")
        return False


def _extract_zip(archive_path: Path, dest_dir: Path) -> bool:
    """Extract a .zip archive to the destination directory."""
    try:
        import zipfile
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dest_dir)
        return True
    except (zipfile.ZipError, IOError, OSError) as e:
        log.error(f"Failed to extract {archive_path}: {e}")
        return False


def _find_binary_in_dir(binary_name: str, search_dir: Path) -> Optional[Path]:
    """Find a binary file in a directory (recursively, first match)."""
    for path in search_dir.rglob(binary_name):
        if path.is_file():
            return path
    return None


def _find_asset(assets: dict[str, dict[str, str]], tag: str, platform: str, backend: str) -> Optional[dict[str, str]]:
    """Find the appropriate asset for a platform/backend combination.
    
    Tries multiple naming patterns since asset names vary (e.g., openvino includes version, sycl includes fp precision).
    """
    patterns = []
    
    if platform == "windows":
        # Windows assets (.zip)
        patterns = [
            f"llama-{tag}-bin-win-{backend}-x64.zip",
            f"llama-{tag}-bin-win-{backend}-2026.4.1-x64.zip",  # openvino version
        ]
        # Also search by substring match as fallback
        for name, info in assets.items():
            if f"-bin-win-{backend}-" in name and "x64" in name and name.endswith(".zip"):
                patterns.append(name)
    else:
        # Linux assets (.tar.gz)
        patterns = [
            f"llama-{tag}-bin-ubuntu-{backend}-x64.tar.gz",
            f"llama-{tag}-bin-ubuntu-{backend}-fp32-x64.tar.gz",  # sycl fp32
            f"llama-{tag}-bin-ubuntu-{backend}-fp16-x64.tar.gz",  # sycl fp16
            f"llama-{tag}-bin-ubuntu-{backend}-2026.4.1-x64.tar.gz",  # openvino version
        ]
        # Also search by substring match as fallback
        for name, info in assets.items():
            if f"-bin-ubuntu-{backend}" in name and "x64" in name and name.endswith(".tar.gz"):
                if name not in patterns:
                    patterns.append(name)
    
    for pattern in patterns:
        if pattern in assets:
            return assets[pattern]
    
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Main update function
# ──────────────────────────────────────────────────────────────────────────────

def update_backends(
    tag: Optional[str] = None,
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> dict[str, Any]:
    """Update all llama.cpp backends to the specified tag.
    
    Args:
        tag: The release tag to update to (e.g., "b12189"). 
             If None, fetches the latest nightly build automatically.
        progress_callback: Optional callback(tag: str, percent: int) for progress updates.
    
    Returns:
        dict with status information including:
        - success: bool
        - tag: str|None
        - errors: list[str]
        - updated_combinations: list[str]
        - config_updated: bool
    """
    errors: list[str] = []
    updated_combinations: list[str] = []
    
    # Step 1: Determine the tag to use
    if tag is None:
        if progress_callback:
            progress_callback("Fetching latest nightly tag...", 5)
        tag = _get_latest_nightly_tag()
        if tag is None:
            errors.append("Failed to fetch latest nightly tag from GitHub")
            return {"success": False, "tag": None, "errors": errors, "updated_combinations": [], "config_updated": False}
        if progress_callback:
            progress_callback(f"Using nightly build: {tag}", 10)
    
    if progress_callback:
        progress_callback(f"Processing tag: {tag}", 15)
    
    # Step 2: Get release assets
    assets = _get_release_assets(tag)
    if not assets:
        errors.append(f"Failed to fetch assets for tag {tag}")
        return {"success": False, "tag": tag, "errors": errors, "updated_combinations": [], "config_updated": False}
    
    if progress_callback:
        progress_callback(f"Found {len(assets)} assets, starting downloads...", 20)
    
    # Step 4: Download and extract binaries for each platform/backend
    tmp_dir = Path(tempfile.mkdtemp(prefix="llama_updater_"))
    
    # Load existing config to respect user-configured paths
    existing_config = _load_config()
    
    def _config_to_target_dir(platform: str, backend: str, config_path: str) -> Path:
        """Convert a config path to a target Path for file operations."""
        if platform == "windows":
            # Config stores Windows paths like "C:/llama.cpp-vulkan/bin"
            # Convert to WSL path for file operations: /mnt/c/llama.cpp-vulkan/bin
            if config_path and config_path.startswith("C:/"):
                # Replace "C:" with "/mnt/c" to get proper WSL path
                return Path("/mnt/c" + config_path[2:].replace("\\", "/"))
            elif config_path and config_path.startswith("/mnt/c/"):
                return Path(config_path)
            else:
                # Fallback to default
                return Path(f"/mnt/c/llama.cpp-{backend}/bin")
        else:
            # Linux: use path as-is, or default to ~/llama.cpp-{backend}/bin
            if config_path and config_path.startswith("/"):
                return Path(config_path)
            else:
                home_dir = Path.home()
                return home_dir / f"llama.cpp-{backend}" / "bin"
    
    def _target_dir_to_config_path(platform: str, backend: str, target_dir: Path) -> str:
        """Convert a target Path back to config format."""
        if platform == "windows":
            # Convert WSL path back to Windows format for config
            # /mnt/c/llama.cpp-vulkan/bin -> C:/llama.cpp-vulkan/bin
            path_str = str(target_dir)
            if path_str.startswith("/mnt/c/"):
                return "C:/" + path_str[7:]
            elif path_str.startswith("C:/"):
                return path_str.replace("\\", "/")
            else:
                return f"C:/llama.cpp-{backend}/bin"
        else:
            # Linux: store absolute path
            return str(target_dir)
    
    try:
        # Map each combination to its platform and backend
        combo_map = {
            "windows-vulkan": ("windows", "vulkan"),
            "windows-sycl": ("windows", "sycl"),
            "windows-openvino": ("windows", "openvino"),
            "linux-vulkan": ("linux", "vulkan"),
            "linux-sycl": ("linux", "sycl"),
            "linux-openvino": ("linux", "openvino"),
        }
        
        for combo in PLATFORM_BACKEND_COMBINATIONS:
            if progress_callback:
                progress_percent = 20 + int(80 * PLATFORM_BACKEND_COMBINATIONS.index(combo) / len(PLATFORM_BACKEND_COMBINATIONS))
                progress_callback(f"Processing {combo}...", progress_percent)
            
            platform, backend = combo_map[combo]
            
            # Find the appropriate asset using pattern matching
            asset_found = _find_asset(assets, tag, platform, backend)
            if not asset_found:
                errors.append(f"{platform.capitalize()} {backend} binary not found in assets for tag {tag}")
                continue
            
            filename = asset_found["name"]
            
            # Determine target directory from existing config (user-configured path)
            existing_config_path = existing_config.get(combo, "")
            target_dir = _config_to_target_dir(platform, backend, existing_config_path)
            
            if platform == "windows":
                # Windows: zip files
                # Download the file
                download_path = tmp_dir / filename
                if progress_callback:
                    progress_callback(f"Downloading Windows {backend} binary...", 25)
                
                try:
                    urllib.request.urlretrieve(asset_found["url"], download_path)
                    if progress_callback:
                        progress_callback(f"Extracting Windows {backend} binary...", 35)
                except Exception as e:
                    errors.append(f"Failed to download Windows {backend} binary: {e}")
                    continue
                
                # Extract the zip
                extract_dir = tmp_dir / f"win_extract_{backend}"
                extract_dir.mkdir(exist_ok=True)
                
                if not _extract_zip(download_path, extract_dir):
                    errors.append(f"Failed to extract Windows {backend} binary")
                    continue
                
                # Find the llama-server.exe to verify the archive is valid
                server_bin = _find_binary_in_dir("llama-server.exe", extract_dir)
                if not server_bin:
                    errors.append(f"llama-server.exe not found in Windows {backend} archive")
                    continue
                
                # Copy ALL extracted files to user-configured target directory
                # (Windows llama.cpp uses a small llama-server.exe loader + DLLs for implementation)
                target_dir.mkdir(parents=True, exist_ok=True)
                for item in extract_dir.rglob('*'):
                    if item.is_file():
                        # Preserve directory structure relative to extract_dir
                        rel_path = item.relative_to(extract_dir)
                        dest = target_dir / rel_path
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, dest)
                
                # Make all extracted Windows binaries executable (chmod +x)
                # This ensures llama-server.exe, llama-tokenize.exe, llama-cli.exe, etc.
                # can be executed on WSL (Windows executables need +x on WSL)
                for item in target_dir.rglob('*'):
                    if item.is_file() and not item.name.endswith(('.dll', '.txt', '.md', '.json', '.h', '.hpp')):
                        # Make .exe files and other binaries executable
                        try:
                            item.chmod(item.stat().st_mode | 0o111)  # Add execute permissions
                        except Exception:
                            pass  # Ignore chmod failures
                
                # Store Windows path in config (C:/ format)
                config_path = _target_dir_to_config_path(platform, backend, target_dir)
                _set_platform_backend_dir(platform, backend, config_path)
                updated_combinations.append(combo)
                
            else:  # linux
                # Linux: tar.gz files (asset already found above)
                # Download the file
                download_path = tmp_dir / filename
                if progress_callback:
                    progress_callback(f"Downloading Linux {backend} binary...", 25)
                
                try:
                    urllib.request.urlretrieve(asset_found["url"], download_path)
                    if progress_callback:
                        progress_callback(f"Extracting Linux {backend} binary...", 35)
                except Exception as e:
                    errors.append(f"Failed to download Linux {backend} binary: {e}")
                    continue
                
                # Extract the tar.gz directly to target directory (like install.sh does with --strip-components=1)
                # Use user-configured target directory
                
                # Clean target directory first (like install.sh does with rm -rf)
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                target_dir.mkdir(parents=True, exist_ok=True)
                
                # Extract with strip_components=1 equivalent: extract to temp, then move contents
                extract_dir = tmp_dir / f"linux_extract_{backend}"
                extract_dir.mkdir(exist_ok=True)
                
                if not _extract_tar_gz(download_path, extract_dir):
                    errors.append(f"Failed to extract Linux {backend} binary")
                    continue
                
                # Find the top-level directory in the archive (usually llama-{tag}-bin-ubuntu-{backend}-x64)
                extracted_items = list(extract_dir.iterdir())
                if len(extracted_items) == 1 and extracted_items[0].is_dir():
                    # Archive has a single top-level directory - move its contents (strip_components=1)
                    top_level = extracted_items[0]
                    for item in top_level.iterdir():
                        shutil.move(str(item), str(target_dir / item.name))
                else:
                    # No top-level directory, move everything directly
                    for item in extracted_items:
                        shutil.move(str(item), str(target_dir / item.name))
                
                # Make all extracted binaries executable (chmod +x)
                # This ensures llama-server, llama-tokenize, llama-cli, etc. can be executed
                for item in target_dir.rglob('*'):
                    if item.is_file() and not item.name.endswith(('.so', '.dll', '.txt', '.md', '.json', '.h', '.hpp')):
                        # Check if it's an executable (no extension or known binary extensions)
                        # We'll make everything executable except shared libraries and text files
                        try:
                            item.chmod(item.stat().st_mode | 0o111)  # Add execute permissions
                        except Exception:
                            pass  # Ignore chmod failures
                
                # Verify llama-server exists in the target
                server_bin = target_dir / "llama-server"
                if not server_bin.exists():
                    errors.append(f"llama-server binary not found in extracted Linux {backend} archive")
                    continue
                
                # For SYCL backend, create a wrapper script that sources Intel oneAPI env
                if backend == "sycl":
                    # Rename original binary
                    orig_bin = target_dir / "llama-server-orig"
                    if orig_bin.exists():
                        orig_bin.unlink()
                    server_bin.rename(orig_bin)
                    
                    # Create wrapper script
                    wrapper_path = target_dir / "llama-server-sycl"
                    wrapper_content = '''#!/usr/bin/env bash
# Wrapper script for llama-server with SYCL support
# Sources Intel oneAPI environment to provide libsvml.so and other required libraries

# Source Intel oneAPI environment
if [[ -f /opt/intel/oneapi/setvars.sh ]]; then
    source /opt/intel/oneapi/setvars.sh >/dev/null 2>&1
fi

# Also set LD_LIBRARY_PATH explicitly for libsvml.so
export LD_LIBRARY_PATH="/opt/intel/oneapi/compiler/2026.2/lib:${LD_LIBRARY_PATH}"

# Run the actual llama-server (original binary)
exec "$(dirname "$0")/llama-server-orig" "$@"
'''
                    wrapper_path.write_text(wrapper_content)
                    wrapper_path.chmod(0o755)
                    
                    # Create symlink llama-server -> llama-server-sycl
                    server_bin.symlink_to("llama-server-sycl")
                    
                    log.info(f"Created SYCL wrapper for {backend} backend")
                
                # Store the Linux path in config (as absolute path)
                config_path = _target_dir_to_config_path(platform, backend, target_dir)
                _set_platform_backend_dir(platform, backend, config_path)
                updated_combinations.append(combo)
        
        # Step 5: Save the updated config, recording the release tag that is
        # now installed so the UI can report the "currently running" release.
        config = _load_config()
        config["llama_release"] = tag
        if progress_callback:
            progress_callback("Saving configuration...", 95)
        
        _save_config(config)
        config_updated = True
        
        result = {
            "success": len(errors) == 0,
            "tag": tag,
            "errors": errors,
            "updated_combinations": updated_combinations,
            "config_updated": config_updated,
            "total_combinations": len(PLATFORM_BACKEND_COMBINATIONS),
        }
        
        if progress_callback:
            progress_callback("Update complete!", 100)
        
        # Cleanup
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass
        
        return result
        
    except Exception as e:
        errors.append(f"Unexpected error during update: {e}")
        if progress_callback:
            progress_callback(f"Error: {e}", 0)
        
        # Cleanup on error
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
        
        return {"success": False, "tag": tag, "errors": errors, "updated_combinations": [], "config_updated": False}


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point (optional)
# ──────────────────────────────────────────────────────────────────────────────

def main():
    """CLI entry point for manual backend updates."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Update llama.cpp backends")
    parser.add_argument(
        "--tag",
        type=str,
        help="Release tag to update to (e.g., b12189). If omitted, fetches latest nightly.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Show progress output",
    )
    
    args = parser.parse_args()
    
    def progress(tag: str, percent: int):
        bar = "=" * (percent // 2) + " " * (50 - percent // 2)
        print(f"\r[{bar}] {percent}% {tag}", end="", flush=True)
    
    result = update_backends(tag=args.tag, progress_callback=progress if args.progress else None)
    
    print()
    if result["success"]:
        print(f"Successfully updated backends to tag {result['tag']}")
        print(f"Updated {len(result['updated_combinations'])} / {result['total_combinations']} combinations:")
        for combo in result["updated_combinations"]:
            print(f"  - {combo}: {_get_platform_backend_dir(combo.split('-')[0], combo.split('-')[1])}")
    else:
        print(f"Failed to update backends:")
        for error in result["errors"]:
            print(f"  - {error}")


if __name__ == "__main__":
    main()