from __future__ import annotations

import json
import os
import sys
import uuid

from gi.repository import Gio, GLib

from .glibutil import attach_timeout, close_connection, destroy_source, unsubscribe


def _session_connection() -> Gio.DBusConnection:
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not addr:
        addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
    return Gio.DBusConnection.new_for_address_sync(
        addr,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None,
        None,
    )


def grab_uri(timeout_sec: int = 8, connection: Gio.DBusConnection | None = None) -> str:
    owns_ctx = False
    owns_bus = connection is None
    ctx = GLib.MainContext.get_thread_default()
    if ctx is None:
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        owns_ctx = True
    loop = GLib.MainLoop(ctx)
    box: dict = {"uri": "", "error": ""}
    bus = connection or _session_connection()
    unique = bus.get_unique_name() or ":0.0"
    sender = unique[1:].replace(".", "_")
    token = f"kuaishot{uuid.uuid4().hex[:10]}"
    handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    sub_id = None
    timer = None

    def on_signal(_conn, _sender, _path, _iface, signal, parameters):
        if signal != "Response":
            return
        code, data = parameters.unpack()
        if code == 0:
            box["uri"] = str(data.get("uri") or "")
        else:
            box["error"] = f"portal Response={code}"
        loop.quit()

    try:
        sub_id = bus.signal_subscribe(
            None,
            "org.freedesktop.portal.Request",
            "Response",
            handle,
            None,
            Gio.DBusSignalFlags.NONE,
            on_signal,
        )
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Screenshot",
            None,
        )
        options = {
            "interactive": GLib.Variant("b", False),
            "modal": GLib.Variant("b", False),
            "handle_token": GLib.Variant("s", token),
        }
        proxy.call_sync(
            "Screenshot",
            GLib.Variant("(sa{sv})", ("", options)),
            Gio.DBusCallFlags.NONE,
            15000,
            None,
        )
        timer = attach_timeout(ctx, max(1, int(timeout_sec)) * 1000, loop.quit)
        loop.run()
    finally:
        destroy_source(timer)
        unsubscribe(bus, sub_id)
        if owns_ctx:
            try:
                ctx.pop_thread_default()
            except Exception:
                pass
        if owns_bus:
            close_connection(bus)
    if box["error"]:
        raise RuntimeError(box["error"])
    if not box["uri"]:
        raise RuntimeError("portal 未返回截图")
    return box["uri"]


def main() -> int:
    try:
        uri = grab_uri()
        sys.stdout.write(json.dumps({"ok": True, "uri": uri}))
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
