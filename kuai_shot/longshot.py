from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime

from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .capture import shots_from_frames
from .compose import compose_shots
from .display import monitor_names_for_rect, probe_monitors
from .log import event, exception
from .mutter_capture import RegionCaster
from .paths import pictures_dir
from .stitch import frame_usable, frames_similar, stitch_long


def last_usable(frames: list[QImage]) -> QImage | None:
    for image in reversed(frames):
        if image is not None and not image.isNull():
            return image
    return None


class _LiveGrabber:
    def __init__(
        self,
        region: QRect,
        first: QImage | None,
        on_new: Callable[[QImage], None],
        paused: Callable[[], bool],
    ):
        self.region = QRect(region)
        self._last = first
        self.on_new = on_new
        self.paused = paused
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="kuai-shot-long-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        event("long.grab.stop")
        self._stop.set()

    def _run(self) -> None:
        event("long.grab.start", x=self.region.x(), y=self.region.y(), w=self.region.width(), h=self.region.height())
        caster = RegionCaster()
        try:
            names = monitor_names_for_rect(self.region)
            caster.start(names=names or None)
            event("long.grab.caster_ok", monitors=",".join(names) if names else "all")
            paused = False
            while not self._stop.is_set():
                if self.paused():
                    if not paused:
                        event("long.grab.pause")
                        paused = True
                    self._stop.wait(0.2)
                    continue
                if paused:
                    event("long.grab.resume")
                    paused = False
                image = None
                try:
                    frames = caster.grab_frames()
                    shots = shots_from_frames(frames, probe_monitors())
                    image = compose_shots(shots, self.region)
                except Exception as exc:
                    exception("long.grab.fail", exc)
                    image = None
                usable = frame_usable(image)
                similar = frames_similar(self._last, image) if usable else False
                if usable and not similar:
                    self._last = image
                    event("long.grab.accept", w=image.width(), h=image.height())
                    self.on_new(image)
                else:
                    event(
                        "long.grab.skip",
                        reason="empty" if image is None or image.isNull() else ("unusable" if not usable else "similar"),
                        w=0 if image is None or image.isNull() else image.width(),
                        h=0 if image is None or image.isNull() else image.height(),
                    )
                self._stop.wait(0.05)
        except Exception as exc:
            exception("long.grab.caster", exc)
        finally:
            try:
                caster.stop()
            except Exception:
                pass
            event("long.grab.end")


class LongShotBar(QWidget):
    frame_arrived = pyqtSignal(object)

    def __init__(
        self,
        region: QRect,
        first: QImage | None,
        on_copy: Callable[[QImage | None], None],
        on_cancel: Callable[[], None],
        axis: str = "v",
    ):
        super().__init__()
        self.region = QRect(region)
        self.axis = "h" if axis == "h" else "v"
        self.frames: list[QImage] = [first] if first is not None and not first.isNull() else []
        self.on_copy = on_copy
        self.on_cancel = on_cancel
        self._closed = False
        self._hover = False
        self._grabber: _LiveGrabber | None = None

        self.setWindowTitle("快截图 · 长图")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self.title = QLabel(self._title())
        self.preview = QLabel()
        self.preview.setMinimumSize(160, 90)
        hint = QLabel("遮罩已关掉。在刚才选的区域里滚动，预览变了就是记下了。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9aa6b2;")

        save_btn = QPushButton("保存")
        copy_btn = QPushButton("✓")
        cancel_btn = QPushButton("取消")
        save_btn.setFixedHeight(32)
        copy_btn.setFixedHeight(32)
        cancel_btn.setFixedHeight(32)
        save_btn.clicked.connect(self._save)
        copy_btn.clicked.connect(self._copy)
        cancel_btn.clicked.connect(self._cancel)

        row = QHBoxLayout()
        row.addWidget(save_btn)
        row.addWidget(copy_btn)
        row.addWidget(cancel_btn)
        row.addStretch(1)

        root = QVBoxLayout(self)
        root.addWidget(self.title)
        root.addWidget(self.preview)
        root.addWidget(hint)
        root.addLayout(row)
        self.setStyleSheet("QWidget { background:#1a1f27; color:#e8edf2; } QPushButton { min-width:64px; }")
        self.resize(360, 260)
        self._update_preview()
        self.frame_arrived.connect(self._on_frame)
        first = self.frames[-1] if self.frames else None
        self._grabber = _LiveGrabber(self.region, first, self.frame_arrived.emit, lambda: self._hover or self._closed)
        self.show()
        self.raise_()
        self.activateWindow()
        event("long.bar.ready", axis=self.axis, frames=len(self.frames))

    def export(self) -> QImage | None:
        image = stitch_long(self.frames, self.axis)
        if image is not None and not image.isNull():
            return image
        return last_usable(self.frames)

    def _title(self) -> str:
        r = self.region
        axis = "横向" if self.axis == "h" else "纵向"
        return f"长图{axis}  ·  已记下 {len(self.frames)} 帧  ·  {r.width()}×{r.height()}"

    def _update_preview(self) -> None:
        self.title.setText(self._title())
        if not self.frames:
            return
        image = self.frames[-1]
        pix = QPixmap.fromImage(image)
        self.preview.setPixmap(pix.scaled(280, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def enterEvent(self, ev) -> None:
        self._hover = True
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover = False
        super().leaveEvent(ev)

    def closeEvent(self, ev) -> None:
        if not self._closed:
            self._cancel()
        ev.accept()

    def _on_frame(self, image: object) -> None:
        if self._closed or not isinstance(image, QImage) or image.isNull():
            event("long.bar.frame_drop", closed=int(self._closed))
            return
        self.frames.append(image)
        event("long.bar.frame", n=len(self.frames), w=image.width(), h=image.height())
        self._update_preview()

    def destroy_ui(self) -> None:
        self._closed = True
        if self._grabber is not None:
            self._grabber.stop()
            self._grabber = None
        self.hide()
        self.close()

    def _stop_grab(self) -> None:
        if self._grabber is not None:
            self._grabber.stop()
            self._grabber = None

    def _copy(self) -> None:
        event("long.ui.copy", closed=int(self._closed), frames=len(self.frames))
        if self._closed:
            return
        self._closed = True
        self._stop_grab()
        image = self.export()
        event(
            "long.ui.copy.export",
            has_image=int(image is not None and not image.isNull()),
            w=0 if image is None or image.isNull() else image.width(),
            h=0 if image is None or image.isNull() else image.height(),
        )
        self.hide()
        self.on_copy(image)

    def _save(self) -> None:
        event("long.ui.save", closed=int(self._closed), frames=len(self.frames))
        if self._closed:
            return
        self._stop_grab()
        image = self.export()
        if image is None or image.isNull():
            event("long.ui.save.empty")
            return
        suggested = str(pictures_dir() / datetime.now().strftime("截图-%Y%m%d-%H%M%S.png"))
        path, _ok = QFileDialog.getSaveFileName(self, "保存截图", suggested, "PNG (*.png)")
        event("long.ui.save.dialog", path=path or "")
        if not path:
            if not self._closed:
                last = self.frames[-1] if self.frames else None
                self._grabber = _LiveGrabber(
                    self.region, last, self.frame_arrived.emit, lambda: self._hover or self._closed
                )
                event("long.ui.save.resume")
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        ok = image.save(path, "PNG")
        event("long.ui.save.write", path=path, ok=int(ok))
        self._closed = True
        self.hide()
        self.on_copy(image)

    def _cancel(self) -> None:
        event("long.ui.cancel", closed=int(self._closed))
        if self._closed:
            return
        self._closed = True
        self._stop_grab()
        self.hide()
        self.on_cancel()
