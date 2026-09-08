from __future__ import annotations

from . import APP_ID


# xdg-desktop-portal 用 desktop id（不含 .desktop）当 app key。
# 快捷键、自启动、托盘守护进程可能对应不同识别名，一并写入。
APP_KEYS = (
    APP_ID,
    f"{APP_ID}.desktop",
)


def grant_screenshot_permission() -> None:
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    proxy = Gio.DBusProxy.new_sync(
        bus,
        Gio.DBusProxyFlags.NONE,
        None,
        "org.freedesktop.impl.portal.PermissionStore",
        "/org/freedesktop/impl/portal/PermissionStore",
        "org.freedesktop.impl.portal.PermissionStore",
        None,
    )
    for app in APP_KEYS:
        proxy.call_sync(
            "SetPermission",
            GLib.Variant.new_tuple(
                GLib.Variant("s", "screenshot"),
                GLib.Variant("b", True),
                GLib.Variant("s", "screenshot"),
                GLib.Variant("s", app),
                GLib.Variant("as", ["yes"]),
            ),
            Gio.DBusCallFlags.NONE,
            4000,
            None,
        )


def screenshot_granted(app: str = APP_ID) -> bool:
    from gi.repository import Gio, GLib

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.freedesktop.impl.portal.PermissionStore",
            "/org/freedesktop/impl/portal/PermissionStore",
            "org.freedesktop.impl.portal.PermissionStore",
            None,
        )
        result = proxy.call_sync(
            "GetPermission",
            GLib.Variant("(sss)", ("screenshot", "screenshot", app)),
            Gio.DBusCallFlags.NONE,
            4000,
            None,
        )
        perms = list(result.unpack()[0])
        return "yes" in perms
    except Exception:
        return False
