from __future__ import annotations

import subprocess
from pathlib import Path

from . import APP_ID, APP_NAME, HOTKEY


GNOME_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_PREFIX = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
BINDING_PATH = f"{CUSTOM_PREFIX}/kuai-shot/"
OLD_BINDING_PATHS = (
    f"{CUSTOM_PREFIX}/feishu-shot/",
)


def _gsettings(*args: str) -> str:
    result = subprocess.run(
        ["gsettings", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _parse_list(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw or raw == "@as []" or raw == "[]":
        return []
    items = []
    for part in raw.strip("[]").split(","):
        part = part.strip().strip("'").strip('"')
        if part:
            items.append(part)
    return items


def register_gnome_hotkey(command: str, name: str = "快截图") -> None:
    existing = _parse_list(_gsettings("get", GNOME_SCHEMA, "custom-keybindings"))
    changed = False
    for old in OLD_BINDING_PATHS:
        if old in existing:
            existing = [p for p in existing if p != old]
            subprocess.run(
                ["gsettings", "reset", f"{GNOME_SCHEMA}.custom-keybinding:{old}", "name"],
                check=False,
            )
            subprocess.run(
                ["gsettings", "reset", f"{GNOME_SCHEMA}.custom-keybinding:{old}", "command"],
                check=False,
            )
            subprocess.run(
                ["gsettings", "reset", f"{GNOME_SCHEMA}.custom-keybinding:{old}", "binding"],
                check=False,
            )
            changed = True
    if BINDING_PATH not in existing:
        existing.append(BINDING_PATH)
        changed = True
    if changed:
        encoded = "[" + ", ".join(f"'{p}'" for p in existing) + "]"
        subprocess.run(
            ["gsettings", "set", GNOME_SCHEMA, "custom-keybindings", encoded],
            check=False,
        )
    schema = f"{GNOME_SCHEMA}.custom-keybinding:{BINDING_PATH}"
    subprocess.run(["gsettings", "set", schema, "name", name], check=False)
    subprocess.run(["gsettings", "set", schema, "command", command], check=False)
    subprocess.run(["gsettings", "set", schema, "binding", HOTKEY], check=False)


def _desktop_text(exec_cmd: str, icon: str | None, autostart: bool) -> str:
    icon_line = f"Icon={icon}\n" if icon else ""
    extra = ""
    if autostart:
        extra = "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=2\n"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        f"Name={APP_NAME}\n"
        f"StartupWMClass={APP_ID}\n"
        "Comment=区域截图工具\n"
        "Exec=" + exec_cmd + "\n"
        + icon_line
        + "Terminal=false\n"
        "StartupNotify=false\n"
        "Categories=Utility;Graphics;\n"
        + extra
    )


def write_autostart(exec_cmd: str, icon: str | None = None) -> Path:
    from .paths import autostart_desktop_path

    path = autostart_desktop_path()
    path.write_text(_desktop_text(exec_cmd, icon, autostart=True), encoding="utf-8")
    return path


def write_application_desktop(exec_cmd: str, icon: str | None = None) -> Path:
    from .paths import applications_desktop_path

    path = applications_desktop_path()
    path.write_text(_desktop_text(exec_cmd, icon, autostart=False), encoding="utf-8")
    return path
