from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import APP_ID, APP_NAME, HOTKEY_LABEL
from .capture import CaptureError, capture_shots, start_grabber
from .display import probe_monitors, split_by_screens
from .hotkey import register_gnome_hotkey, write_application_desktop, write_autostart
from .ipc import CommandServer, send_command
from .log import event, exception
from .overlay_gtk import OverlaySession, ensure_gtk, pump_gtk
from .paths import install_root, log_path
from .permission import grant_screenshot_permission


class ShotApp(QObject):
    ipc_cmd = pyqtSignal(str)

    def __init__(self, qt: QApplication):
        super().__init__()
        self.qt = qt
        self.session = None
        self.pins: list = []
        self._busy = False
        self._clip_image = None
        self._clip_mime = None
        self._portal_job: dict | None = None
        self.ipc_cmd.connect(self._on_ipc)
        self.server = CommandServer(self.ipc_cmd.emit)
        self.server.start()
        start_grabber()
        ensure_gtk()
        self._glib = QTimer()
        self._glib.timeout.connect(pump_gtk)
        self._glib.start(5)
        self.tray = self._make_tray()
        event("app.start", log=str(log_path()))
        qt.screenAdded.connect(self._on_screens_changed)
        qt.screenRemoved.connect(self._on_screens_changed)
        qt.primaryScreenChanged.connect(self._on_screens_changed)

    def _on_screens_changed(self, *_args) -> None:
        if self.session is not None:
            self.session.close_all()

    def _icon(self) -> QIcon:
        icon_path = install_root() / "icon.png"
        if icon_path.exists():
            return QIcon(str(icon_path))
        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)
        from PyQt5.QtGui import QColor, QPainter, QPen

        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(QColor("#3370FF"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
        painter.setPen(QPen(QColor("#FFFFFF"), 4))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(16, 18, 32, 24, 4, 4)
        painter.end()
        return QIcon(pix)

    def _make_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(self._icon())
        tray.setToolTip(f"{APP_NAME}  {HOTKEY_LABEL}")
        menu = QMenu()
        menu.addAction(f"截图（{HOTKEY_LABEL}）", self.start_capture)
        menu.addSeparator()
        menu.addAction("退出", self.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        tray.show()
        return tray

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.start_capture()

    def _on_ipc(self, cmd: str) -> None:
        if cmd == "capture":
            self._arm_capture()
        elif cmd == "quit":
            QTimer.singleShot(0, self.quit)

    def _arm_capture(self) -> None:
        if self.session is not None or self._busy:
            return
        self.start_capture()

    def start_capture(self) -> None:
        if self.session is not None or self._busy:
            event("app.capture.skip", busy=int(self._busy), has_session=int(self.session is not None))
            return
        event("app.capture.begin")
        self._busy = True
        try:
            grabber = start_grabber()
            job = self._portal_job
            self._portal_job = None
            if job is None:
                job = grabber.submit()
            monitors = probe_monitors()
            if not monitors:
                raise CaptureError("没有可用的屏幕")
            shots = []
            try:
                full = grabber.wait_image(job, timeout=10.0)
                shots = split_by_screens(full, monitors)
            except CaptureError:
                shots = []
            if not shots:
                shots = capture_shots(monitors, skip_portal=False)
            if not shots:
                raise CaptureError("没有可用的屏幕")
            self.session = OverlaySession(shots)
            self.session.finished.connect(self._on_overlay_finished)
            event("app.capture.overlay", shots=len(shots))
        except Exception as exc:
            self._busy = False
            exception("app.capture.fail", exc)
            self.tray.showMessage(APP_NAME, str(exc), QSystemTrayIcon.Warning, 4000)

    def _on_overlay_finished(self) -> None:
        session = self.session
        if session is not None:
            self.pins.extend(getattr(session, "pins", []) or [])
            result = getattr(session, "result", None)
            event(
                "app.finished",
                has_result=int(result is not None and not result.isNull()),
                w=0 if result is None or result.isNull() else result.width(),
                h=0 if result is None or result.isNull() else result.height(),
            )
            if result is not None and not result.isNull():
                from .overlay_gtk import _CLIP_HOLD, copy_image

                self._clip_image = result.copy()
                copy_image(self._clip_image)
                self._clip_mime = _CLIP_HOLD.get("mime")
                held = self._clip_image
                QTimer.singleShot(120, lambda: self._reoffer_clip(held))
        self.session = None
        self._busy = False
        event("app.idle")

    def _reoffer_clip(self, image) -> None:
        if image is None or image.isNull():
            return
        from .overlay_gtk import _CLIP_HOLD, copy_image

        copy_image(image)
        self._clip_mime = _CLIP_HOLD.get("mime")
        event("app.clip.reoffer", w=image.width(), h=image.height())

    def quit(self) -> None:
        self.server.stop()
        self.qt.quit()


def run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"

    capture_only = "--capture" in argv or "capture" in argv
    if capture_only and send_command("capture"):
        return 0
    if "--quit" in argv and send_command("quit"):
        return 0
    if not capture_only and send_command("ping"):
        return 0

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    QApplication.setDesktopFileName(APP_ID)
    qt = QApplication(argv)
    qt.setApplicationName(APP_ID)
    qt.setApplicationDisplayName(APP_NAME)
    qt.setQuitOnLastWindowClosed(False)

    app = ShotApp(qt)
    if capture_only:
        QTimer.singleShot(80, app.start_capture)
    else:
        app.tray.showMessage(APP_NAME, f"已在后台运行，按 {HOTKEY_LABEL} 截图", QSystemTrayIcon.Information, 2500)

    return qt.exec_()


def install_user_integration(launcher: str) -> None:
    icon = str(install_root() / "icon.png")
    icon = icon if Path(icon).exists() else None
    write_autostart(f"{launcher} daemon", icon)
    write_application_desktop(f"{launcher} capture", icon)
    register_gnome_hotkey(f"{launcher} capture", APP_NAME)
    try:
        grant_screenshot_permission()
        print("已写入截图权限：kuai-shot")
    except Exception as exc:
        print(f"写入截图权限失败：{exc}")
