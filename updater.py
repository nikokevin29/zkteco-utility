"""
ZKTeco Utility — Auto Updater v2
Strategy:
  1. Download new EXE to temp folder (with progress)
  2. On Windows: rename old → _old.exe, copy new → current name, restart
  3. On Linux/macOS: direct replace + chmod + restart
  4. On startup: cleanup any leftover _old.exe
"""

import os
import sys
import json
import shutil
import ssl
import tempfile
import threading
import urllib.request
from pathlib import Path

GITHUB_OWNER = "xbanana29"
GITHUB_REPO  = "zkteco-utility"
API_URL      = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# Asset names per platform
ASSET_NAME = {
    "win32":  "ZKTeco_Utility.exe",
    "darwin": "ZKTeco_Utility_macOS",
    "linux":  "ZKTeco_Utility_Linux",
}


def _ssl_context():
    """Use Mozilla CA bundle when available (avoids expired Windows root CA bugs)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _version_tuple(v: str):
    try:
        return tuple(int(x) for x in v.lstrip('v').split('.'))
    except Exception:
        return (0,)


def get_latest_release() -> dict | None:
    """Fetch latest release info from GitHub API."""
    try:
        req = urllib.request.Request(
            API_URL,
            headers={"User-Agent": "ZKTeco-Utility-Updater"}
        )
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode())

        tag    = data.get("tag_name", "")
        body   = data.get("body", "")
        assets = data.get("assets", [])

        platform_asset = ASSET_NAME.get(sys.platform, ASSET_NAME["win32"])
        dl_url = next(
            (a["browser_download_url"] for a in assets
             if a["name"].lower() == platform_asset.lower()),
            None
        )
        if not tag or not dl_url:
            return None
        return {"version": tag, "download_url": dl_url, "body": body}
    except Exception:
        return None


def is_newer(latest_ver: str, current_ver: str) -> bool:
    return _version_tuple(latest_ver) > _version_tuple(current_ver)


def cleanup_old_exe():
    """
    Called at app startup — remove leftover _old.exe from previous update.
    Safe to call even if no old file exists.
    """
    if not getattr(sys, 'frozen', False):
        return  # running as .py, skip
    current = Path(sys.executable)
    old_exe = current.parent / (current.stem + '_old' + current.suffix)
    if old_exe.exists():
        try:
            old_exe.unlink()
        except Exception:
            pass  # will be cleaned up next time


def _current_exe() -> Path | None:
    """Return path to running EXE, or None if running as .py script."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable)
    return None


def download_and_replace(
    download_url: str,
    on_progress=None,   # callback(pct: int)
    on_status=None,     # callback(msg: str)
    on_done=None,       # callback()
    on_error=None       # callback(msg: str)
):
    """
    Download new EXE and replace current binary.
    All callbacks are called from background thread —
    caller must use .after() if updating tkinter widgets.
    """
    def _status(msg):
        if on_status: on_status(msg)

    def _run():
        try:
            current_exe = _current_exe()

            # ── Step 1: Download ──────────────────────────────────────────
            _status("Connecting to GitHub...")
            tmp_dir  = tempfile.mkdtemp(prefix="zkteco_update_")
            tmp_exe  = os.path.join(tmp_dir, os.path.basename(download_url))

            req = urllib.request.Request(
                download_url,
                headers={"User-Agent": "ZKTeco-Utility-Updater"}
            )
            with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as resp:
                total      = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 65536

                _status("Downloading update...")
                with open(tmp_exe, 'wb') as f:
                    while True:
                        buf = resp.read(chunk_size)
                        if not buf: break
                        f.write(buf)
                        downloaded += len(buf)
                        if total and on_progress:
                            on_progress(int(downloaded / total * 100))

            if on_progress: on_progress(100)

            # ── Step 2: Verify download not empty ─────────────────────────
            if os.path.getsize(tmp_exe) < 1024:
                raise RuntimeError("Downloaded file is too small — possibly corrupted.")

            # ── Step 3: Replace ───────────────────────────────────────────
            if current_exe is None:
                # Running as .py script — just report done, no replace needed
                _status("Download complete (running as script, no replace needed).")
                if on_done: on_done()
                return

            _status("Installing update...")

            if sys.platform == 'win32':
                # Windows: cannot overwrite running EXE directly
                # Rename old → _old.exe, copy new → original name
                old_exe = current_exe.parent / (current_exe.stem + '_old' + current_exe.suffix)

                # Remove previous _old if exists
                if old_exe.exists():
                    try: old_exe.unlink()
                    except Exception: pass

                # Rename current → _old
                current_exe.rename(old_exe)

                # Copy new → current name
                shutil.copy2(tmp_exe, str(current_exe))

            else:
                # Linux/macOS: direct replace
                shutil.copy2(tmp_exe, str(current_exe))
                os.chmod(str(current_exe), 0o755)

            # ── Step 4: Cleanup temp ──────────────────────────────────────
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

            _status("Update installed. Restarting...")
            if on_done: on_done()

        except Exception as e:
            if on_error: on_error(str(e))

    threading.Thread(target=_run, daemon=True).start()


def restart_app():
    """Restart the current process."""
    exe = _current_exe()
    if exe and exe.exists():
        import subprocess
        subprocess.Popen([str(exe)])
    sys.exit(0)
