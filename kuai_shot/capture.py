from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from PyQt5.QtGui import QColor, QGuiApplication, QImage, QImageReader, QPainter, QPixmap

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
        image = load_image_raw(path)
        _safe_unlink(path)
        if image is None:
            raise CaptureError("截图文件无效")
        return image


_grabber: PortalGrabber | None = None


def start_grabber() -> PortalGrabber:
    global _grabber
    if _grabber is None:
        _grabber = PortalGrabber()
    return _grabber


def capture_fullscreen(include_cursor: bool = False) -> QImage:
    del include_cursor
    errors: list[str] = []
    for fn in (_capture_portal, _capture_gnome_shell, _capture_qt):
        try:
            image = fn()
            if image is not None and not image.isNull():
                return image
            errors.append(f"{fn.__name__}: empty")
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")
    raise CaptureError("无法抓取屏幕：" + " | ".join(errors))


def _capture_portal() -> QImage | None:
    grabber = start_grabber()
    try:
        uri = grabber.grab_uri()
    except CaptureError:
        from .portal_helper import grab_uri

        uri = grab_uri()
    path = _uri_to_path(uri)
    image = load_image_raw(path)
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


def wait_for_compositor(seconds: float = 0.0) -> None:
    if seconds > 0:
        time.sleep(seconds)
