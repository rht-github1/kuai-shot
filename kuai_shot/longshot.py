from __future__ import annotations

import threading
import time
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
from .stitch import (
    canvas_viewport,
    estimate_shift,
    extend_unwrapped,
    frame_usable,
    frames_similar,
    stitch_long,
)

MAX_CANVAS = 16384


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
        axis: str = "v",
        canvas: QImage | None = None,
    ):
        self.region = QRect(region)
        self.axis = "h" if axis == "h" else "v"
        seed = canvas if canvas is not None and not canvas.isNull() else first
        self._last = first
        self.canvas = seed.copy() if seed is not None and not seed.isNull() else None
        self.gaps = 0
        self.steps = 0
        self.on_new = on_new
        self.paused = paused
        self._dir = 0
        self._live_ready = False
        self._misses = 0
        self._emit_at = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="kuai-shot-long-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        event("long.grab.stop")
        self._stop.set()

    def snapshot(self) -> QImage | None:
        with self._lock:
            if self.canvas is None or self.canvas.isNull():
                return None
            return self.canvas.copy()

    def _run(self) -> None:
        event("long.grab.start", x=self.region.x(), y=self.region.y(), w=self.region.width(), h=self.region.height())
        caster = RegionCaster()
        try:
            names = monitor_names_for_rect(self.region)
            caster.start(names=names or None, area=self.region)
            event(
                "long.grab.caster_ok",
                monitors=",".join(names) if names else "all",
                area=1 if caster._area is not None else 0,
            )
            paused = False
            while not self._stop.is_set():
                if self.paused():
                    if not paused:
                        event("long.grab.pause")
                        paused = True
                    caster.grab_frames(timeout_ms=40, reuse_last=False)
                    continue
                if paused:
                    event("long.grab.resume")
                    paused = False
                try:
                    frames = caster.grab_frames(timeout_ms=80 if caster._primed else 800, reuse_last=False)
                except Exception as exc:
                    exception("long.grab.fail", exc)
                    frames = []
                image = self._crop(caster, frames)
                if image is not None:
                    self._ingest(image)
                while not self._stop.is_set() and not self.paused():
                    extra = caster.grab_frames(timeout_ms=2, reuse_last=False)
                    if not extra:
                        break
                    image = self._crop(caster, extra)
                    if image is not None:
                        self._ingest(image)
        except Exception as exc:
            exception("long.grab.caster", exc)
        finally:
            try:
                caster.stop()
            except Exception:
                pass
            event("long.grab.end", steps=self.steps, gaps=self.gaps)

    def _crop(self, caster: RegionCaster, frames: list[dict]) -> QImage | None:
        if not frames:
            return None
        try:
            if caster._area is not None:
                image = frames[0].get("image")
                if image is None or image.isNull():
                    return None
                if image.width() != self.region.width() or image.height() != self.region.height():
                    image = image.scaled(self.region.width(), self.region.height())
                return image
            shots = shots_from_frames(frames, probe_monitors())
            return compose_shots(shots, self.region)
        except Exception as exc:
            exception("long.grab.crop", exc)
            return None

    def _ingest(self, image: QImage) -> None:
        if not frame_usable(image):
            event("long.grab.skip", reason="unusable", w=image.width(), h=image.height())
            return
        last = self._last
        if last is None or last.isNull():
            self._last = image
            with self._lock:
                if self.canvas is None or self.canvas.isNull():
                    self.canvas = image.copy()
            self._emit(True)
            return
        if not self._live_ready:
            self._live_ready = True
            self._last = image
            if self.canvas is None or self.canvas.isNull() or not frames_similar(self.canvas, image, limit=18):
                with self._lock:
                    self.canvas = image.copy()
                event("long.grab.resync", reason="first-live", w=image.width(), h=image.height())
            else:
                event("long.grab.resync", reason="first-live-keep", w=image.width(), h=image.height())
            self._emit(True)
            return
        if frames_similar(last, image, limit=10):
            return
        with self._lock:
            tail = canvas_viewport(self.canvas, image, self.axis)
            tail = tail.copy() if tail is not None and not tail.isNull() else None
        shift = estimate_shift(tail if tail is not None else last, image, self.axis)
        if shift is None:
            self._misses += 1
            if self._misses >= 24:
                self._last = image
                self._misses = 0
                event("long.grab.resync", reason="follow", w=image.width(), h=image.height())
            else:
                event("long.grab.skip", reason="no-align", w=image.width(), h=image.height(), misses=self._misses)
            return
        if abs(shift) <= 1:
            self._misses = 0
            return
        if self._dir == 0 and abs(shift) >= 3:
            self._dir = 1 if shift > 0 else -1
        if self._dir and shift * self._dir < 0:
            event("long.grab.skip", reason="reverse", shift=shift)
            return
        self._last = image
        self._misses = 0
        with self._lock:
            canvas = self.canvas if self.canvas is not None and not self.canvas.isNull() else last
            grown = extend_unwrapped(canvas, image, shift, self.axis)
            if self.axis == "h" and grown.width() > MAX_CANVAS:
                event("long.grab.cap", w=grown.width())
                return
            if self.axis != "h" and grown.height() > MAX_CANVAS:
                event("long.grab.cap", h=grown.height())
                return
            self.canvas = grown
            self.steps += 1
        event(
            "long.grab.accept",
            w=image.width(),
            h=image.height(),
            shift=shift,
            canvas_w=self.canvas.width() if self.canvas is not None else 0,
            canvas_h=self.canvas.height() if self.canvas is not None else 0,
        )
        self._emit(False)

    def _emit(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._emit_at < 0.1:
            return
        self._emit_at = now
        image = self.snapshot()
        if image is not None:
            self.on_new(image)


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
        self._canvas = first.copy() if first is not None and not first.isNull() else None
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
        hint = QLabel("对着刚才的选区滚动，长图会跟着长。对不准的帧会跳过，避免重影。")
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
        self._grabber = _LiveGrabber(
            self.region,
            first,
            self.frame_arrived.emit,
            lambda: self._hover or self._closed,
            axis=self.axis,
            canvas=self._canvas,
        )
        self.show()
        self.raise_()
        self.activateWindow()
        event("long.bar.ready", axis=self.axis, frames=len(self.frames))

    def export(self) -> QImage | None:
        grabbed = None
        if self._grabber is not None:
            grabbed = self._grabber.snapshot()
        if grabbed is not None and not grabbed.isNull():
            return grabbed
        if self._canvas is not None and not self._canvas.isNull():
            return self._canvas
        image = stitch_long(self.frames, self.axis)
        if image is not None and not image.isNull():
            return image
        return last_usable(self.frames)

    def _title(self) -> str:
        image = self._canvas
        if image is not None and not image.isNull():
            return f"长图{'横向' if self.axis == 'h' else '纵向'}  ·  {image.width()}×{image.height()}"
        r = self.region
        return f"长图{'横向' if self.axis == 'h' else '纵向'}  ·  {r.width()}×{r.height()}"

    def _update_preview(self) -> None:
        self.title.setText(self._title())
        image = self._canvas
        if image is None or image.isNull():
            return
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
        self._canvas = image
        if self.frames:
            self.frames[-1] = image
        else:
            self.frames.append(image)
        event("long.bar.frame", n=len(self.frames), w=image.width(), h=image.height())
        self._update_preview()

    def destroy_ui(self) -> None:
        self._closed = True
        self._keep_canvas()
        if self._grabber is not None:
            self._grabber.stop()
            self._grabber = None
        self.hide()
        self.close()

    def _keep_canvas(self) -> None:
        if self._grabber is None:
            return
        snapped = self._grabber.snapshot()
        if snapped is not None and not snapped.isNull():
            self._canvas = snapped
            if self.frames:
                self.frames[-1] = snapped
            else:
                self.frames.append(snapped)

    def _stop_grab(self) -> None:
        self._keep_canvas()
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
                last = self._canvas if self._canvas is not None else (self.frames[-1] if self.frames else None)
                self._grabber = _LiveGrabber(
                    self.region,
                    last,
                    self.frame_arrived.emit,
                    lambda: self._hover or self._closed,
                    axis=self.axis,
                    canvas=self._canvas,
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
