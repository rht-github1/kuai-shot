from __future__ import annotations

import os
import queue
import threading
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from PyQt5.QtGui import QColor, QGuiApplication, QImage, QImageReader, QPainter, QPixmap

from .display import Monitor, ScreenShot, probe_monitors, split_by_screens
from .paths import cache_dir


class CaptureError(RuntimeError):
    pass


def _temp_png() -> Path:
    return cache_dir() / f"cap-{os.getpid()}-{uuid.uuid4().hex}.png"


def _uri_to_path(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return unquote(parsed.path)
    return unquote(uri)


def load_image_raw(path: str) -> QImage | None:
    if not path or not os.path.exists(path):
        return None
    reader = QImageReader(path)
    reader.setAutoTransform(False)
    image = reader.read()
    if image.isNull():
        image = QImage(path)
    if image.isNull():
        return None
    if image.format() not in (QImage.Format_RGB32, QImage.Format_ARGB32, QImage.Format_ARGB32_Premultiplied):
        image = image.convertToFormat(QImage.Format_RGB32)
    return image


class PortalGrabber:
    def __init__(self) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="kuai-shot-portal", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from gi.repository import Gio, GLib

        from .portal_helper import grab_uri

        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS") or Gio.dbus_address_get_for_bus_sync(
            Gio.BusType.SESSION, None
        )
        try:
            conn = Gio.DBusConnection.new_for_address_sync(
                addr,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None,
                None,
            )
        except Exception:
            conn = None
        self._ready.set()

        while True:
            box = self._jobs.get()
            if box is None:
                break
            try:
                box["uri"] = grab_uri(timeout_sec=8, connection=conn)
            except Exception as exc:
                box["error"] = str(exc)
            box["done"].set()

    def submit(self) -> dict:
        if not self._ready.wait(2):
            raise CaptureError("截图通道未就绪")
        box = {"done": threading.Event(), "uri": "", "error": ""}
        self._jobs.put(box)
        return box

    def wait_box(self, box: dict, timeout: float = 10.0) -> str:
        if not box["done"].wait(timeout):
            raise CaptureError("截图超时")
        if box["error"]:
            raise CaptureError(box["error"])
        return str(box["uri"])

    def grab_uri(self, timeout: float = 10.0) -> str:
        return self.wait_box(self.submit(), timeout)

    def wait_image(self, box: dict, timeout: float = 10.0) -> QImage:
        path = _uri_to_path(self.wait_box(box, timeout))
        image = _load_captured_file(path)
        if image is None:
            raise CaptureError("截图文件无效")
        return image


_grabber: PortalGrabber | None = None


def start_grabber() -> PortalGrabber:
    global _grabber
    if _grabber is None:
        _grabber = PortalGrabber()
    return _grabber


def _on_wayland() -> bool:
    return (os.environ.get("XDG_SESSION_TYPE") or "").lower() == "wayland"


def _match_frame(name: str, monitors: list[Monitor]) -> Monitor | None:
    for mon in monitors:
        if mon.name == name:
            return mon
    for mon in monitors:
        if name and (name in mon.name or mon.name in name):
            return mon
    return None


def _shots_from_frames(frames: list[dict], monitors: list[Monitor]) -> list[ScreenShot]:
    shots: list[ScreenShot] = []
    used: set[str] = set()
    for item in frames:
        image = item.get("image")
        if image is None or image.isNull():
            continue
        mon = _match_frame(str(item.get("name") or ""), monitors)
        if mon is None or mon.name in used:
            continue
        used.add(mon.name)
        shots.append(ScreenShot(mon.screen, image, mon.logical, mon.name))
    return shots


def capture_shots(
    monitors: list[Monitor] | None = None,
    include_cursor: bool = False,
    skip_portal: bool = False,
) -> list[ScreenShot]:
    monitors = monitors or probe_monitors()
    if not monitors:
        raise CaptureError("没有可用的屏幕")
    if not include_cursor and not skip_portal:
        try:
            full = _capture_portal()
            if full is not None and not full.isNull() and not _mostly_blank(full):
                shots = split_by_screens(full, monitors)
                if shots:
                    return shots
        except Exception:
            pass
    try:
        from .mutter_capture import capture_monitor_frames

        shots = _shots_from_frames(capture_monitor_frames(include_cursor=include_cursor), monitors)
        if shots:
            return shots
    except Exception:
        pass
    full = capture_fullscreen(include_cursor=include_cursor, skip_portal=skip_portal or not include_cursor)
    return split_by_screens(full, monitors)


def capture_fullscreen(include_cursor: bool = False, skip_portal: bool = False) -> QImage:
    errors: list[str] = []
    fns = []
    if not include_cursor and not skip_portal:
        fns.append(_capture_portal)
    fns.append(_capture_mutter)
    fns.append(_capture_gnome_shell)
    if not _on_wayland():
        fns.append(_capture_qt)
    for fn in fns:
        try:
            image = fn(include_cursor) if fn is _capture_mutter else fn()
            if image is not None and not image.isNull():
                if _mostly_blank(image):
                    errors.append(f"{fn.__name__}: blank")
                    continue
                return image
            errors.append(f"{fn.__name__}: empty")
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")
    raise CaptureError("无法抓取屏幕：" + " | ".join(errors))


def _mostly_blank(image: QImage) -> bool:
    w, h = image.width(), image.height()
    if w < 8 or h < 8:
        return True
    step_x = max(1, w // 24)
    step_y = max(1, h // 16)
    total = 0
    dark = 0
    for y in range(0, h, step_y):
        for x in range(0, w, step_x):
            c = image.pixelColor(x, y)
            total += 1
            if c.red() < 10 and c.green() < 10 and c.blue() < 10:
                dark += 1
    return total > 0 and dark / total > 0.98


def _capture_mutter(include_cursor: bool = False) -> QImage | None:
    from .mutter_capture import capture_monitors

    return capture_monitors(include_cursor=include_cursor)


def _capture_portal() -> QImage | None:
    grabber = start_grabber()
    try:
        uri = grabber.grab_uri()
    except CaptureError:
        from .portal_helper import grab_uri

        uri = grab_uri()
    return _load_captured_file(_uri_to_path(uri))


def _load_captured_file(path: str) -> QImage | None:
    image = load_image_raw(path)
    if path:
        _safe_unlink(path)
    return image


def _capture_gnome_shell() -> QImage | None:
    from gi.repository import Gio, GLib

    filename = str(_temp_png())
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    proxy = Gio.DBusProxy.new_sync(
        bus,
        Gio.DBusProxyFlags.NONE,
        None,
        "org.gnome.Shell.Screenshot",
        "/org/gnome/Shell/Screenshot",
        "org.gnome.Shell.Screenshot",
        None,
    )
    result = proxy.call_sync(
        "Screenshot",
        GLib.Variant("(bbs)", (False, False, filename)),
        Gio.DBusCallFlags.NONE,
        8000,
        None,
    )
    success, used = result.unpack()
    path = used or filename
    if not success:
        return None
    image = load_image_raw(path)
    _safe_unlink(path)
    return image


def _capture_qt() -> QImage | None:
    screens = QGuiApplication.screens()
    if not screens:
        return None
    geo = screens[0].virtualGeometry()
    canvas = QPixmap(geo.size())
    canvas.fill(QColor(0, 0, 0))
    painter = QPainter(canvas)
    grabbed = False
    for screen in screens:
        grab = screen.grabWindow(0)
        if grab.isNull():
            continue
        grabbed = True
        painter.drawPixmap(screen.geometry().topLeft() - geo.topLeft(), grab)
    painter.end()
    if not grabbed:
        return None
    return canvas.toImage()


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


