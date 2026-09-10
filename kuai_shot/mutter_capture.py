from __future__ import annotations

import os
import threading

from PyQt5.QtCore import QRect
from PyQt5.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap


class MutterCaptureError(RuntimeError):
    pass


def capture_monitors(include_cursor: bool = False) -> QImage:
    frames = capture_monitor_frames(include_cursor=include_cursor)
    return _stitch([(item["image"], item) for item in frames])


def capture_monitor_frames(include_cursor: bool = False) -> list[dict]:
    box: dict = {"frames": None, "error": ""}
    thread = threading.Thread(
        target=_capture_thread,
        args=(include_cursor, box),
        name="kuai-shot-mutter",
        daemon=True,
    )
    thread.start()
    thread.join(12)
    if thread.is_alive():
        raise MutterCaptureError("Mutter 截图超时")
    if box["error"]:
        raise MutterCaptureError(box["error"])
    frames = box["frames"] or []
    if not frames:
        raise MutterCaptureError("Mutter 未返回图像")
    return frames


def _capture_thread(include_cursor: bool, box: dict) -> None:
    try:
        box["frames"] = _capture_frames_with_glib(include_cursor)
    except Exception as exc:
        box["error"] = str(exc)


class RegionCaster:
    """Keep one Mutter ScreenCast session and pull frames without tearing it down."""

    def __init__(self) -> None:
        self._ctx = None
        self._conn = None
        self._sess = None
        self._streams: list[dict] = []
        self._pipes: list[dict] = []
        self._last: dict[str, QImage] = {}
        self._primed = False
        self._started = False
        self._area: QRect | None = None

    def start(
        self,
        names: list[str] | None = None,
        include_cursor: bool = False,
        area: QRect | None = None,
    ) -> None:
        if self._started:
            return
        if area is not None and area.width() >= 8 and area.height() >= 8:
            try:
                self._boot(include_cursor=include_cursor, names=None, area=QRect(area))
                return
            except Exception:
                self.stop()
        self._boot(include_cursor=include_cursor, names=names, area=None)

    def _boot(
        self,
        include_cursor: bool = False,
        names: list[str] | None = None,
        area: QRect | None = None,
    ) -> None:
        from gi.repository import Gio, GLib

        self._ctx = GLib.MainContext.new()
        self._ctx.push_thread_default()
        try:
            self._conn = _session_connection()
            connectors = _pick_connectors(self._conn, names)
            if not connectors and area is None:
                raise MutterCaptureError("没有可录制的显示器")

            cast = Gio.DBusProxy.new_sync(
                self._conn,
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
            self._sess = Gio.DBusProxy.new_sync(
                self._conn,
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
            if area is not None:
                path = self._sess.call_sync(
                    "RecordArea",
                    GLib.Variant(
                        "(iiiia{sv})",
                        (int(area.x()), int(area.y()), int(area.width()), int(area.height()), props),
                    ),
                    Gio.DBusCallFlags.NONE,
                    4000,
                    None,
                ).unpack()[0]
                streams.append(
                    {
                        "path": path,
                        "name": "area",
                        "node": None,
                        "x": int(area.x()),
                        "y": int(area.y()),
                    }
                )
                self._area = QRect(area)
            else:
                self._area = None
                for name in connectors:
                    path = self._sess.call_sync(
                        "RecordMonitor",
                        GLib.Variant("(sa{sv})", (name, props)),
                        Gio.DBusCallFlags.NONE,
                        4000,
                        None,
                    ).unpack()[0]
                    streams.append({"path": path, "name": name, "node": None, "x": 0, "y": 0})

            pending = {item["path"]: item for item in streams}
            loop = GLib.MainLoop(self._ctx)

            def on_signal(_c, _s, path, _iface, signal, parameters):
                if signal != "PipeWireStreamAdded" or path not in pending:
                    return
                pending[path]["node"] = int(parameters.unpack()[0])
                _fill_geometry(self._conn, pending[path])
                if all(item["node"] is not None for item in streams):
                    loop.quit()

            self._conn.signal_subscribe(
                None,
                "org.gnome.Mutter.ScreenCast.Stream",
                "PipeWireStreamAdded",
                None,
                None,
                Gio.DBusSignalFlags.NONE,
                on_signal,
            )
            GLib.timeout_add(2000, loop.quit)
            self._sess.call_sync("Start", None, Gio.DBusCallFlags.NONE, 4000, None)
            loop.run()
            missing = [item["name"] for item in streams if item["node"] is None]
            if missing:
                raise MutterCaptureError("PipeWire 节点未就绪: " + ", ".join(missing))
            self._streams = streams
            self._open_pipes()
            self._started = True
        except Exception:
            self.stop()
            raise

    def _open_pipes(self) -> None:
        self._close_pipes()
        for item in self._streams:
            pipe, sink = _gst_open(item["node"])
            self._pipes.append({"item": item, "pipe": pipe, "sink": sink})

    def _close_pipes(self) -> None:
        if not self._pipes:
            self._primed = False
            return
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        if not Gst.is_initialized():
            Gst.init(None)
        for entry in self._pipes:
            try:
                entry["pipe"].set_state(Gst.State.NULL)
            except Exception:
                pass
        self._pipes = []
        self._primed = False

    def grab_frames(self, timeout_ms: int | None = None, reuse_last: bool = True) -> list[dict]:
        if not self._started:
            raise MutterCaptureError("ScreenCast 未启动")
        if not self._pipes:
            self._open_pipes()
        timeout = 800 if not self._primed else 120
        if timeout_ms is not None:
            timeout = timeout_ms
        frames = []
        for entry in self._pipes:
            item = entry["item"]
            image = _gst_try_pull(entry["sink"], timeout)
            if image is None and reuse_last:
                image = self._last.get(item["name"])
            if image is None:
                if reuse_last:
                    raise MutterCaptureError("PipeWire 没有输出帧")
                return []
            self._last[item["name"]] = image
            frames.append(
                {
                    "name": item["name"],
                    "image": image,
                    "x": item["x"],
                    "y": item["y"],
                }
            )
        self._primed = True
        return frames

    def stop(self) -> None:
        self._close_pipes()
        if self._sess is not None:
            try:
                self._sess.call_sync("Stop", None, Gio.DBusCallFlags.NONE, 4000, None)
            except Exception:
                pass
        self._sess = None
        self._streams = []
        self._started = False
        self._area = None
        if self._ctx is not None:
            try:
                self._ctx.pop_thread_default()
            except Exception:
                pass
            self._ctx = None
        self._conn = None


def _capture_frames_with_glib(include_cursor: bool) -> list[dict]:
    caster = RegionCaster()
    try:
        caster.start(include_cursor=include_cursor)
        return caster.grab_frames()
    finally:
        caster.stop()


def _pick_connectors(conn, names: list[str] | None) -> list[str]:
    available = _connectors(conn)
    if not names:
        return available
    picked: list[str] = []
    for name in names:
        if not name:
            continue
        if name in available and name not in picked:
            picked.append(name)
            continue
        match = next((item for item in available if name in item or item in name), None)
        if match and match not in picked:
            picked.append(match)
    return picked or available


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


def _gst_open(node_id: int):
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    if not Gst.is_initialized():
        Gst.init(None)

    pipeline = Gst.parse_launch(
        f"pipewiresrc path={int(node_id)} do-timestamp=true ! "
        "videoconvert ! video/x-raw,format=RGBA ! "
        "appsink name=sink max-buffers=24 drop=false sync=false"
    )
    sink = pipeline.get_by_name("sink")
    if sink is None:
        pipeline.set_state(Gst.State.NULL)
        raise MutterCaptureError("GStreamer appsink 不可用")
    pipeline.set_state(Gst.State.PLAYING)
    return pipeline, sink


def _gst_try_pull(sink, timeout_ms: int) -> QImage | None:
    try:
        return _gst_pull(sink, timeout_ms)
    except MutterCaptureError:
        return None


def _gst_pull(sink, timeout_ms: int) -> QImage:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    sample = sink.emit("try-pull-sample", max(1, int(timeout_ms)) * Gst.MSECOND)
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


def _screen_by_name() -> dict[str, object]:
    return {screen.name(): screen for screen in (QGuiApplication.screens() or []) if screen.name()}


def _stitch(parts: list[tuple[QImage, dict]]) -> QImage:
    valid = [(image, stream) for image, stream in parts if image is not None and not image.isNull()]
    if not valid:
        raise MutterCaptureError("所有屏幕抓取失败")
    if len(valid) == 1:
        return valid[0][0]
    screens = _screen_by_name()
    placed: list[tuple[QImage, int, int, int, int]] = []
    for image, stream in valid:
        screen = screens.get(stream.get("name") or "")
        if screen is not None:
            geo = screen.geometry()
            placed.append((image, geo.x(), geo.y(), geo.width(), geo.height()))
        else:
            placed.append((image, int(stream.get("x") or 0), int(stream.get("y") or 0), image.width(), image.height()))
    min_x = min(x for _image, x, _y, _w, _h in placed)
    min_y = min(y for _image, _x, y, _w, _h in placed)
    max_x = max(x + w for _image, x, _y, w, _h in placed)
    max_y = max(y + h for _image, _x, y, _w, h in placed)
    canvas = QPixmap(max(1, max_x - min_x), max(1, max_y - min_y))
    canvas.fill(QColor(0, 0, 0))
    painter = QPainter(canvas)
    for image, x, y, w, h in placed:
        if image.width() != w or image.height() != h:
            painter.drawImage(x - min_x, y - min_y, image.scaled(w, h))
        else:
            painter.drawImage(x - min_x, y - min_y, image)
    painter.end()
    return canvas.toImage()
