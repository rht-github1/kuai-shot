from __future__ import annotations

import os
from pathlib import Path


def user_home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def pictures_dir() -> Path:
    xdg = os.environ.get("XDG_PICTURES_DIR")
    if xdg:
        path = Path(xdg)
        path.mkdir(parents=True, exist_ok=True)
        return path
    try:
        import subprocess

        out = subprocess.check_output(["xdg-user-dir", "PICTURES"], text=True, timeout=2).strip()
        if out:
            path = Path(out)
            path.mkdir(parents=True, exist_ok=True)
            return path
    except Exception:
        pass
    for name in ("图片", "Pictures"):
        path = user_home() / name
        if path.is_dir():
            return path
    path = user_home() / "Pictures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = Path(os.environ.get("XDG_CACHE_HOME", user_home() / ".cache")) / "kuai-shot"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    path = Path(os.environ.get("XDG_CONFIG_HOME", user_home() / ".config")) / "kuai-shot"
    path.mkdir(parents=True, exist_ok=True)
    return path


def socket_path() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/runtime-{os.getuid()}"))
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime / "kuai-shot.sock"


def autostart_desktop_path() -> Path:
    path = Path(os.environ.get("XDG_CONFIG_HOME", user_home() / ".config")) / "autostart"
    path.mkdir(parents=True, exist_ok=True)
    return path / "kuai-shot.desktop"


def applications_desktop_path() -> Path:
    path = Path(os.environ.get("XDG_DATA_HOME", user_home() / ".local" / "share")) / "applications"
    path.mkdir(parents=True, exist_ok=True)
    return path / "kuai-shot.desktop"


def install_root() -> Path:
    return Path(os.environ.get("KUAI_SHOT_HOME", user_home() / ".local" / "share" / "kuai-shot"))
