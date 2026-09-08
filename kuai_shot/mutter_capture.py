from __future__ import annotations

import os
import threading

from PyQt5.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap


class MutterCaptureError(RuntimeError):
    pass


def capture_monitors(include_cursor: bool = False) -> QImage:
    box: dict = {"image": None, "error": ""}
    thread = threading.Thread(
        target=_capture_thread,
        args=(include_cursor, box),
        name="kuai-shot-mutter",
        daemon=True,
    )
    thread.start()
    thread.join(10)
    if thread.is_alive():
        raise MutterCaptureError("Mutter 截图超时")
    if box["error"]:
        raise MutterCaptureError(box["error"])
    image = box["image"]
    if image is None or image.isNull():
        raise MutterCaptureError("Mutter 未返回图像")
    return image


def _capture_thread(include_cursor: bool, box: dict) -> None:
    try:
        box["image"] = _capture_with_glib(include_cursor)
    except Exception as exc:
        box["error"] = str(exc)


def _capture_with_glib(include_cursor: bool) -> QImage:
    from gi.repository import Gio, GLib

    ctx = GLib.MainContext.new()
    ctx.push_thread_default()
    try:
        conn = _session_connection()
        connectors = _connectors(conn)
        if not connectors:
            raise MutterCaptureError("没有可录制的显示器")

        cast = Gio.DBusProxy.new_sync(
            conn,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.gnome.Mutter.ScreenCast",
            "/org/gnome/Mutter/ScreenCast",
            "org.gnome.Mutter.ScreenCast",
            None,
        )
        sess_path = cast.call_sync(
            "CreateSession",
            GLib.Variant("(a{sv})", ({"disable-animations": GLib.Variant("b", True)},)),
            Gio.DBusCallFlags.NONE,
            4000,
            None,
        ).unpack()[0]
        sess = Gio.DBusProxy.new_sync(
            conn,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.gnome.Mutter.ScreenCast",
            sess_path,
            "org.gnome.Mutter.ScreenCast.Session",
            None,
        )
        cursor_mode = 1 if include_cursor else 0
        props = {"cursor-mode": GLib.Variant("u", cursor_mode)}
        streams: list[dict] = []
        for name in connectors:
            path = sess.call_sync(
                "RecordMonitor",
                GLib.Variant("(sa{sv})", (name, props)),
                Gio.DBusCallFlags.NONE,
                4000,
                None,
            ).unpack()[0]
            streams.append({"path": path, "name": name, "node": None, "x": 0, "y": 0})

        pending = {item["path"]: item for item in streams}
        loop = GLib.MainLoop(ctx)

        def on_signal(_c, _s, path, _iface, signal, parameters):
            if signal != "PipeWireStreamAdded" or path not in pending:
                return
            pending[path]["node"] = int(parameters.unpack()[0])
            _fill_geometry(conn, pending[path])
            if all(item["node"] is not None for item in streams):
                loop.quit()

        conn.signal_subscribe(
            None,
            "org.gnome.Mutter.ScreenCast.Stream",
            "PipeWireStreamAdded",
            None,
            None,
            Gio.DBusSignalFlags.NONE,
            on_signal,
        )
        GLib.timeout_add(5000, loop.quit)
        sess.call_sync("Start", None, Gio.DBusCallFlags.NONE, 4000, None)
        loop.run()
        missing = [item["name"] for item in streams if item["node"] is None]
        if missing:
            raise MutterCaptureError("PipeWire 节点未就绪: " + ", ".join(missing))
        frames = [(_gst_grab(item["node"]), item) for item in streams]
        try:
            sess.call_sync("Stop", None, Gio.DBusCallFlags.NONE, 4000, None)
        except Exception:
            pass
        return _stitch(frames)
    finally:
        try:
            ctx.pop_thread_default()
        except Exception:
            pass


def _session_connection():
    from gi.repository import Gio

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


def _connectors(conn) -> list[str]:
    names = _display_config_connectors(conn)
    if names:
        return names
    screens = list(QGuiApplication.screens() or [])
    return [screen.name() for screen in screens if screen.name()]


def _display_config_connectors(conn) -> list[str]:
    from gi.repository import Gio

    try:
        proxy = Gio.DBusProxy.new_sync(
            conn,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.gnome.Mutter.DisplayConfig",
            "/org/gnome/Mutter/DisplayConfig",
            "org.gnome.Mutter.DisplayConfig",
            None,
        )
        result = proxy.call_sync("GetCurrentState", None, Gio.DBusCallFlags.NONE, 3000, None)
        _serial, _monitors, logical, _props = result.unpack()
        names: list[str] = []
        for item in logical:
            for mon in item[5]:
                connector = str(mon[0])
                if connector and connector not in names:
                    names.append(connector)
        return names
    except Exception:
        return []


def _fill_geometry(conn, stream: dict) -> None:
    from gi.repository import Gio, GLib

    try:
        raw = conn.call_sync(
            "org.gnome.Mutter.ScreenCast",
            stream["path"],
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant("(ss)", ("org.gnome.Mutter.ScreenCast.Stream", "Parameters")),
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        ).unpack()[0]
        pos = raw.get("position")
        if pos:
            stream["x"], stream["y"] = int(pos[0]), int(pos[1])
    except Exception:
        pass


def _gst_grab(node_id: int) -> QImage:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    if not Gst.is_initialized():
        Gst.init(None)

    pipeline = Gst.parse_launch(
        f"pipewiresrc path={int(node_id)} do-timestamp=true num-buffers=1 ! "
        "videoconvert ! video/x-raw,format=RGBA ! appsink name=sink max-buffers=1 sync=false"
    )
    sink = pipeline.get_by_name("sink")
    if sink is None:
        raise MutterCaptureError("GStreamer appsink 不可用")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        sample = sink.emit("try-pull-sample", 5 * Gst.SECOND)
        if sample is None:
            raise MutterCaptureError("PipeWire 没有输出帧")
        buf = sample.get_buffer()
        caps = sample.get_caps()
        info = caps.get_structure(0)
        width = int(info.get_value("width"))
        height = int(info.get_value("height"))
        ok, mapped = buf.map(Gst.MapFlags.READ)
        if not ok:
            raise MutterCaptureError("无法读取 PipeWire 帧")
        try:
            raw = bytes(mapped.data)
        finally:
            buf.unmap(mapped)
        stride = width * 4
        if height > 0 and len(raw) >= width * height * 4:
            stride = max(stride, len(raw) // height)
        image = QImage(raw, width, height, stride, QImage.Format_RGBA8888).copy()
        if image.isNull():
            raise MutterCaptureError("PipeWire 帧无效")
        return image.convertToFormat(QImage.Format_RGB32)
    finally:
        pipeline.set_state(Gst.State.NULL)


def _stitch(parts: list[tuple[QImage, dict]]) -> QImage:
    valid = [(image, stream) for image, stream in parts if image is not None and not image.isNull()]
    if not valid:
        raise MutterCaptureError("所有屏幕抓取失败")
    if len(valid) == 1:
        return valid[0][0]
    min_x = min(stream["x"] for _image, stream in valid)
    min_y = min(stream["y"] for _image, stream in valid)
    max_x = max(stream["x"] + image.width() for image, stream in valid)
    max_y = max(stream["y"] + image.height() for image, stream in valid)
    canvas = QPixmap(max(1, max_x - min_x), max(1, max_y - min_y))
    canvas.fill(QColor(0, 0, 0))
    painter = QPainter(canvas)
    for image, stream in valid:
        painter.drawImage(stream["x"] - min_x, stream["y"] - min_y, image)
    painter.end()
    return canvas.toImage()
