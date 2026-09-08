from __future__ import annotations

from dataclasses import dataclass, field

from PyQt5.QtCore import QObject, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QImage,
    QImageWriter,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygon,
)
from PyQt5.QtWidgets import (
    QApplication,
    QColorDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QToolButton,
    QWidget,
)

from .display import ScreenShot, fit_pixmap, probe_monitors, screen_under_cursor, ui_scale_for
from .paths import pictures_dir


COLORS = [
    QColor("#F54A45"),
    QColor("#FF8D1A"),
    QColor("#FFC60A"),
    QColor("#34C724"),
    QColor("#3370FF"),
    QColor("#7B67EE"),
    QColor("#FFFFFF"),
    QColor("#1F2329"),
]


@dataclass
class Stroke:
    kind: str
    color: QColor
    width: int
    points: list[QPoint] = field(default_factory=list)
    text: str = ""
    mosaic: int = 12

    def copy(self) -> "Stroke":
        return Stroke(
            kind=self.kind,
            color=QColor(self.color),
            width=self.width,
            points=[QPoint(p) for p in self.points],
            text=self.text,
            mosaic=self.mosaic,
        )


class Toolbar(QWidget):
    toolChanged = pyqtSignal(str)
    colorChanged = pyqtSignal(object)
    widthChanged = pyqtSignal(int)
    undoRequested = pyqtSignal()
    redoRequested = pyqtSignal()
    saveRequested = pyqtSignal()
    copyRequested = pyqtSignal()
    pinRequested = pyqtSignal()
    cancelRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            """
            QWidget#ShotToolbar {
                background: #2B2F36;
                border-radius: 8px;
            }
            QToolButton, QPushButton {
                color: #E8EAED;
                background: transparent;
                border: none;
                padding: 6px 8px;
                border-radius: 6px;
                font-size: 13px;
            }
            QToolButton:hover, QPushButton:hover { background: #3C4149; }
            QToolButton:checked { background: #3370FF; }
            QLabel { color: #9AA0A6; font-size: 12px; }
            """
        )
        self.setObjectName("ShotToolbar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        self.buttons: dict[str, QToolButton] = {}
        for key, label, tip in (
            ("rect", "□", "矩形"),
            ("ellipse", "○", "椭圆"),
            ("arrow", "↗", "箭头"),
            ("pen", "✎", "画笔"),
            ("highlight", "🖍", "高亮"),
            ("mosaic", "▦", "马赛克"),
            ("text", "T", "文字"),
        ):
            btn = QToolButton()
            btn.setText(label)
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, k=key: self._select_tool(k))
            self.buttons[key] = btn
            layout.addWidget(btn)

        layout.addWidget(self._sep())
        self.color_btns: list[QToolButton] = []
        for color in COLORS:
            btn = QToolButton()
            btn.setFixedSize(18, 18)
            btn.setStyleSheet(
                f"QToolButton {{ background:{color.name()}; border-radius:9px; border:1px solid #111; }}"
                "QToolButton:checked { border: 2px solid #fff; }"
            )
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, c=color: self._select_color(c))
            self.color_btns.append(btn)
            layout.addWidget(btn)
        more = QToolButton()
        more.setText("…")
        more.setToolTip("更多颜色")
        more.clicked.connect(self._more_color)
        layout.addWidget(more)

        layout.addWidget(self._sep())
        for w, label in ((2, "细"), (4, "中"), (8, "粗")):
            btn = QToolButton()
            btn.setText(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, width=w: self._select_width(width))
            self.buttons[f"w{w}"] = btn
            layout.addWidget(btn)

        layout.addWidget(self._sep())
        for key, label, tip, signal in (
            ("undo", "↩", "撤销", self.undoRequested),
            ("redo", "↪", "重做", self.redoRequested),
            ("save", "💾", "保存", self.saveRequested),
            ("pin", "📌", "钉在屏幕上", self.pinRequested),
            ("copy", "✓", "复制并关闭", self.copyRequested),
            ("cancel", "✕", "取消", self.cancelRequested),
        ):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(signal)
            layout.addWidget(btn)

        self._select_tool("rect")
        self._select_color(COLORS[0])
        self._select_width(4)

    def _sep(self) -> QLabel:
        lab = QLabel("│")
        lab.setStyleSheet("color:#5A6068; padding:0 4px;")
        return lab

    def _select_tool(self, key: str) -> None:
        for name, btn in self.buttons.items():
            if name in {"rect", "ellipse", "arrow", "pen", "highlight", "mosaic", "text"}:
                btn.setChecked(name == key)
        self.toolChanged.emit(key)

    def _select_color(self, color: QColor) -> None:
        for btn, c in zip(self.color_btns, COLORS):
            btn.setChecked(c.name() == color.name())
        self.colorChanged.emit(color)

    def _select_width(self, width: int) -> None:
        for w in (2, 4, 8):
            self.buttons[f"w{w}"].setChecked(w == width)
        self.widthChanged.emit(width)

    def _more_color(self) -> None:
        color = QColorDialog.getColor(COLORS[0], self, "选择颜色")
        if color.isValid():
            self.colorChanged.emit(color)


class PinWindow(QWidget):
    def __init__(self, pixmap: QPixmap):
        super().__init__(None)
        self._drag: QPoint | None = None
        flags = (
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry().size()
            pixmap = fit_pixmap(pixmap, QSize(max(120, avail.width() - 48), max(120, avail.height() - 48)))
        self._pix = pixmap
        self.setFixedSize(pixmap.size())
        self.setCursor(Qt.SizeAllCursor)
        self.setWindowTitle("钉住的截图")
        if screen is not None:
            handle = self.windowHandle()
            if handle is not None:
                handle.setScreen(screen)
        self.show()
        self.raise_()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pix)
        painter.setPen(QPen(QColor("#3370FF"), 2))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPos() - self.frameGeometry().topLeft()
        elif event.button() == Qt.RightButton:
            self.close()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag = None

    def mouseDoubleClickEvent(self, _event: QMouseEvent) -> None:
        self.close()


class OverlaySession(QObject):
    finished = pyqtSignal()

    def __init__(self, shots: list[ScreenShot]):
        super().__init__()
        self.overlays: list[Overlay] = []
        self.pins: list[PinWindow] = []
        self._closing = False
        for shot in shots:
            overlay = Overlay(shot, self)
            overlay.accepted.connect(self._on_accepted)
            overlay.cancelled.connect(self._on_cancelled)
            self.overlays.append(overlay)
        if self.overlays:
            target = screen_under_cursor()
            for overlay in self.overlays:
                if overlay.screen_obj == target or overlay.screen_obj.geometry().contains(QCursor.pos()):
                    overlay.activateWindow()
                    overlay.setFocus(Qt.OtherFocusReason)
                    break
            else:
                self.overlays[0].activateWindow()
                self.overlays[0].setFocus(Qt.OtherFocusReason)

    def overlay_by_index(self, index: int) -> Overlay | None:
        if 0 <= index < len(self.overlays):
            return self.overlays[index]
        return None

    def clear_others(self, keeper: "Overlay") -> None:
        for overlay in self.overlays:
            if overlay is not keeper:
                overlay.clear_selection()

    def _on_accepted(self, pin: PinWindow | None) -> None:
        if self._closing:
            return
        if pin is not None:
            self.pins.append(pin)
        self.close_all()

    def _on_cancelled(self) -> None:
        if self._closing:
            return
        self.close_all()

    def close_all(self) -> None:
        self._closing = True
        for overlay in self.overlays:
            overlay.blockSignals(True)
            overlay.hide()
            overlay.close()
        self.overlays.clear()
        self.finished.emit()


class Overlay(QWidget):
    accepted = pyqtSignal(object)
    cancelled = pyqtSignal()

    def __init__(self, shot: ScreenShot, session: OverlaySession):
        super().__init__(None)
        self.session = session
        self.screen_obj = shot.screen
        self._image = shot.image
        self._shot = self._pixmap_1x(shot.image)
        self._selecting = False
        self._moving = False
        self._resize_handle = ""
        self._start = QPoint()
        self._rect = QRect()
        self._move_anchor = QPoint()
        self._rect_anchor = QRect()
        self._tool = "rect"
        self._color = QColor(COLORS[0])
        self._width = 4
        self._strokes: list[Stroke] = []
        self._redo: list[Stroke] = []
        self._draft: Stroke | None = None

        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.winId()
        if self.windowHandle() is not None:
            self.windowHandle().setScreen(self.screen_obj)
        self.setGeometry(self.screen_obj.geometry())

        self.toolbar = Toolbar(self)
        self.toolbar.hide()
        self.toolbar.toolChanged.connect(self._on_tool)
        self.toolbar.colorChanged.connect(self._on_color)
        self.toolbar.widthChanged.connect(self._on_width)
        self.toolbar.undoRequested.connect(self._undo)
        self.toolbar.redoRequested.connect(self._redo_stroke)
        self.toolbar.saveRequested.connect(self._save)
        self.toolbar.copyRequested.connect(self._copy)
        self.toolbar.pinRequested.connect(self._pin)
        self.toolbar.cancelRequested.connect(self._cancel)

        self.size_label = QLabel(self)
        self.size_label.setStyleSheet(
            "background:#2B2F36; color:#fff; border-radius:4px; padding:2px 6px; font-size:12px;"
        )
        self.size_label.hide()

        monitors = probe_monitors()
        total = max(1, len(monitors))
        index = next((i for i, m in enumerate(monitors) if m.screen == self.screen_obj), 0)
        scale = ui_scale_for(self.screen_obj.size(), float(self.screen_obj.devicePixelRatio() or 1))
        pad = max(10, int(14 * scale))
        font = max(12, int(13 * scale))
        name = self.screen_obj.name() or f"屏{index + 1}"
        self.hint = QLabel(
            f"当前屏 {name}  {self._image.width()}×{self._image.height()}  ·  跟随鼠标 · 拖拽选区",
            self,
        )
        self.hint.setStyleSheet(
            f"background:#2B2F36; color:#E8EAED; border-radius:6px; padding:{int(6*scale)}px {int(10*scale)}px; font-size:{font}px;"
        )
        self.hint.adjustSize()
        self._hint_pad = pad
        self.hint.move(pad, pad)
        self._index = index

        self.show()
        self.raise_()

    def _pixmap_1x(self, image: QImage) -> QPixmap:
        pix = QPixmap.fromImage(image)
        pix.setDevicePixelRatio(1.0)
        return pix

    def _buffer_scale(self) -> float:
        """Qt Wayland 会把所有窗口都标成最大 DPR。外接 1x 屏窗口常是 2，必须压回去。"""
        output = float(self.screen_obj.devicePixelRatio() or 1.0)
        window = output
        handle = self.windowHandle()
        if handle is not None:
            window = float(handle.devicePixelRatio() or output)
        if window <= 0.01:
            window = 1.0
        return output / window

    def _image_rect(self, view: QRect) -> QRect:
        sx = self._image.width() / max(1, self.width())
        sy = self._image.height() / max(1, self.height())
        return QRect(
            int(round(view.x() * sx)),
            int(round(view.y() * sy)),
            max(1, int(round(view.width() * sx))),
            max(1, int(round(view.height() * sy))),
        ).intersected(self._image.rect())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(self.screen_obj)
        self.setGeometry(self.screen_obj.geometry())
        self.raise_()

    def clear_selection(self) -> None:
        self._rect = QRect()
        self._strokes.clear()
        self._draft = None
        self.toolbar.hide()
        self.size_label.hide()
        self.update()

    def _on_tool(self, tool: str) -> None:
        self._tool = tool
        self.setCursor(Qt.IBeamCursor if tool == "text" else Qt.CrossCursor)

    def _on_color(self, color: QColor) -> None:
        self._color = QColor(color)

    def _on_width(self, width: int) -> None:
        self._width = int(width)

    def _norm_rect(self) -> QRect:
        return self._rect.normalized()

    def _to_image(self, pos: QPoint) -> QPoint:
        r = self._norm_rect()
        if r.width() <= 0 or r.height() <= 0:
            return QPoint()
        sx = self._shot.width() / max(1, self.width())
        sy = self._shot.height() / max(1, self.height())
        return QPoint(int((pos.x()) * sx) - int(r.x() * sx), int((pos.y()) * sy) - int(r.y() * sy))

    def _crop_image(self) -> QImage:
        r = self._norm_rect()
        src = self._image_rect(r)
        img = self._image.copy(src)
        if img.format() != QImage.Format_ARGB32:
            img = img.convertToFormat(QImage.Format_ARGB32)
        if self._strokes:
            painter = QPainter(img)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            for stroke in self._strokes:
                self._paint_stroke(painter, stroke, QSize(img.width(), img.height()), r.size())
            painter.end()
        return img

    def _paint_stroke(self, painter: QPainter, stroke: Stroke, img_size: QSize, view_size: QSize) -> None:
        if not stroke.points:
            return
        sx = img_size.width() / max(1, view_size.width())
        sy = img_size.height() / max(1, view_size.height())

        def mp(p: QPoint) -> QPoint:
            return QPoint(int(p.x() * sx), int(p.y() * sy))

        pts = [mp(p) for p in stroke.points]
        pen = QPen(stroke.color, max(1, int(stroke.width * (sx + sy) / 2)), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        if stroke.kind == "pen" and len(pts) >= 2:
            path = QPainterPath(pts[0])
            for p in pts[1:]:
                path.lineTo(p)
            painter.drawPath(path)
        elif stroke.kind in {"rect", "ellipse", "highlight"} and len(pts) >= 2:
            box = QRect(pts[0], pts[-1]).normalized()
            if stroke.kind == "highlight":
                color = QColor(stroke.color)
                color.setAlpha(88)
                painter.fillRect(box, color)
            elif stroke.kind == "rect":
                painter.drawRect(box)
            else:
                painter.drawEllipse(box)
        elif stroke.kind == "arrow" and len(pts) >= 2:
            self._draw_arrow(painter, pts[0], pts[-1], pen)
        elif stroke.kind == "mosaic" and len(pts) >= 2:
            box = QRect(pts[0], pts[-1]).normalized()
            self._draw_mosaic(painter, box, max(6, int(stroke.mosaic * max(sx, sy))))
        elif stroke.kind == "text":
            font = QFont("Sans", max(10, int(16 * (sx + sy) / 2)))
            painter.setFont(font)
            painter.drawText(pts[0], stroke.text)

    def _draw_arrow(self, painter: QPainter, start: QPoint, end: QPoint, pen: QPen) -> None:
        painter.setPen(pen)
        painter.drawLine(start, end)
        import math

        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        size = max(10, pen.width() * 3)
        p1 = QPoint(
            int(end.x() - size * math.cos(angle - 0.4)),
            int(end.y() - size * math.sin(angle - 0.4)),
        )
        p2 = QPoint(
            int(end.x() - size * math.cos(angle + 0.4)),
            int(end.y() - size * math.sin(angle + 0.4)),
        )
        painter.setBrush(pen.color())
        painter.drawPolygon(QPolygon([end, p1, p2]))

    def _draw_mosaic(self, painter: QPainter, box: QRect, block: int) -> None:
        if box.isEmpty():
            return
        img = painter.device()
        if not isinstance(img, QImage):
            painter.fillRect(box, QColor(120, 120, 120, 90))
            return
        area = box.intersected(img.rect())
        if area.isEmpty():
            return
        tile = max(6, block)
        small = img.copy(area).scaled(
            max(1, area.width() // tile),
            max(1, area.height() // tile),
            Qt.IgnoreAspectRatio,
            Qt.FastTransformation,
        )
        mosaic = small.scaled(area.size(), Qt.IgnoreAspectRatio, Qt.FastTransformation)
        painter.drawImage(area.topLeft(), mosaic)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        scale = self._buffer_scale()
        if abs(scale - 1.0) > 1e-3:
            painter.scale(scale, scale)
        painter.drawImage(0, 0, self._image)
        dim = QColor(0, 0, 0, 150)
        r = self._norm_rect()
        if r.width() > 2 and r.height() > 2:
            path = QPainterPath()
            path.addRect(float(self.rect().x()), float(self.rect().y()), float(self.rect().width()), float(self.rect().height()))
            hole = QPainterPath()
            hole.addRect(float(r.x()), float(r.y()), float(r.width()), float(r.height()))
            painter.fillPath(path.subtracted(hole), dim)
            painter.setPen(QPen(QColor("#3370FF"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(r.adjusted(0, 0, -1, -1))
            self._paint_handles(painter, r)
            painter.save()
            painter.setClipRect(r)
            painter.translate(r.topLeft())
            painter.setRenderHint(QPainter.Antialiasing, True)
            for stroke in self._strokes + ([self._draft] if self._draft else []):
                self._paint_stroke(painter, stroke, r.size(), r.size())
            painter.restore()
        else:
            painter.fillRect(self.rect(), dim)
        if self._selecting:
            self._paint_magnifier(painter)

    def _paint_handles(self, painter: QPainter, r: QRect) -> None:
        painter.setBrush(QColor("#FFFFFF"))
        painter.setPen(QPen(QColor("#3370FF"), 1))
        for h in self._handles(r).values():
            painter.drawRect(h)

    def _handles(self, r: QRect) -> dict[str, QRect]:
        s = 8
        cx, cy = r.center().x(), r.center().y()
        return {
            "nw": QRect(r.left() - s // 2, r.top() - s // 2, s, s),
            "n": QRect(cx - s // 2, r.top() - s // 2, s, s),
            "ne": QRect(r.right() - s // 2, r.top() - s // 2, s, s),
            "e": QRect(r.right() - s // 2, cy - s // 2, s, s),
            "se": QRect(r.right() - s // 2, r.bottom() - s // 2, s, s),
            "s": QRect(cx - s // 2, r.bottom() - s // 2, s, s),
            "sw": QRect(r.left() - s // 2, r.bottom() - s // 2, s, s),
            "w": QRect(r.left() - s // 2, cy - s // 2, s, s),
        }

    def _hit_handle(self, pos: QPoint) -> str:
        if self._norm_rect().width() < 8:
            return ""
        for name, box in self._handles(self._norm_rect()).items():
            if box.adjusted(-3, -3, 3, 3).contains(pos):
                return name
        return ""

    def _paint_magnifier(self, painter: QPainter) -> None:
        if self._norm_rect().width() > 8 and not self._selecting:
            return
        pos = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(pos):
            return
        zoom = 2
        src = 48
        dest = 96
        sx = self._image.width() / max(1, self.width())
        sy = self._image.height() / max(1, self.height())
        cx, cy = int(pos.x() * sx), int(pos.y() * sy)
        box = QRect(cx - src // 2, cy - src // 2, src, src)
        piece = QPixmap.fromImage(self._image.copy(box))
        if piece.isNull():
            return
        mx, my = pos.x() + 24, pos.y() + 24
        if mx + dest > self.width():
            mx = pos.x() - dest - 24
        if my + dest > self.height():
            my = pos.y() - dest - 24
        painter.drawPixmap(QRect(mx, my, dest, dest), piece)
        painter.setPen(QPen(QColor("#3370FF"), 2))
        painter.drawRect(mx, my, dest, dest)
        painter.drawLine(mx + dest // 2, my, mx + dest // 2, my + dest)
        painter.drawLine(mx, my + dest // 2, mx + dest, my + dest // 2)
        painter.fillRect(mx, my + dest + 2, dest, 20, QColor("#2B2F36"))
        painter.setPen(QColor("#fff"))
        color = QColor(self._image.pixel(min(max(cx, 0), self._image.width() - 1), min(max(cy, 0), self._image.height() - 1)))
        painter.drawText(mx + 6, my + dest + 16, f"{pos.x()},{pos.y()}  {color.name().upper()}")
        del zoom

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.RightButton:
            self._cancel()
            return
        if event.button() != Qt.LeftButton:
            return
        pos = event.pos()
        r = self._norm_rect()
        if r.width() > 8:
            handle = self._hit_handle(pos)
            if handle:
                self._resize_handle = handle
                self._rect_anchor = QRect(r)
                return
            if r.contains(pos) and self._tool in {"rect", "ellipse", "arrow", "pen", "highlight", "mosaic", "text"}:
                if self._tool == "text":
                    self._add_text(pos - r.topLeft())
                    return
                self._draft = Stroke(self._tool, QColor(self._color), self._width, [pos - r.topLeft()])
                return
            if r.contains(pos):
                self._moving = True
                self._move_anchor = pos
                self._rect_anchor = QRect(r)
                return
        self.session.clear_others(self)
        self._selecting = True
        self._start = pos
        self._rect = QRect(pos, pos)
        self.toolbar.hide()
        self.hint.hide()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.pos()
        if self._selecting:
            self._rect = QRect(self._start, pos).normalized().intersected(self.rect())
            self._update_chrome()
            self.update()
            return
        if self._resize_handle:
            self._apply_resize(pos)
            self._update_chrome()
            self.update()
            return
        if self._moving:
            delta = pos - self._move_anchor
            self._rect = self._rect_anchor.translated(delta).intersected(self.rect())
            self._update_chrome()
            self.update()
            return
        if self._draft is not None:
            local = pos - self._norm_rect().topLeft()
            if self._draft.kind == "pen":
                self._draft.points.append(local)
            else:
                if len(self._draft.points) == 1:
                    self._draft.points.append(local)
                else:
                    self._draft.points[-1] = local
            self.update()
            return
        handle = self._hit_handle(pos)
        cursors = {
            "nw": Qt.SizeFDiagCursor,
            "se": Qt.SizeFDiagCursor,
            "ne": Qt.SizeBDiagCursor,
            "sw": Qt.SizeBDiagCursor,
            "n": Qt.SizeVerCursor,
            "s": Qt.SizeVerCursor,
            "e": Qt.SizeHorCursor,
            "w": Qt.SizeHorCursor,
        }
        if handle:
            self.setCursor(cursors[handle])
        elif self._norm_rect().contains(pos):
            self.setCursor(Qt.IBeamCursor if self._tool == "text" else Qt.CrossCursor)
        else:
            self.setCursor(Qt.CrossCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        if self._draft is not None:
            if len(self._draft.points) >= 2 or self._draft.kind == "text":
                self._strokes.append(self._draft)
                self._redo.clear()
            self._draft = None
        self._selecting = False
        self._moving = False
        self._resize_handle = ""
        self._rect = self._norm_rect()
        if self._rect.width() > 4 and self._rect.height() > 4:
            self._update_chrome()
            self.toolbar.show()
            self.toolbar.raise_()
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._norm_rect().contains(event.pos()) and self._norm_rect().width() > 4:
            self._copy()
            return
        self.session.clear_others(self)
        self._rect = self.rect()
        self._copy()

    def _apply_resize(self, pos: QPoint) -> None:
        r = QRect(self._rect_anchor)
        h = self._resize_handle
        if "n" in h:
            r.setTop(pos.y())
        if "s" in h:
            r.setBottom(pos.y())
        if "w" in h:
            r.setLeft(pos.x())
        if "e" in h:
            r.setRight(pos.x())
        self._rect = r.normalized().intersected(self.rect())

    def _update_chrome(self) -> None:
        r = self._norm_rect()
        if r.width() <= 2:
            self.size_label.hide()
            return
        out = self._image_rect(r)
        self.size_label.setText(f"{out.width()} × {out.height()}")
        self.size_label.adjustSize()
        scale = self._buffer_scale()
        x = r.x()
        y = r.y() - self.size_label.height() - 6
        if y < 4:
            y = r.y() + 6
        self.size_label.move(max(4, int(x * scale)), max(4, int(y * scale)))
        self.size_label.show()
        tw = self.toolbar.sizeHint().width()
        th = self.toolbar.sizeHint().height()
        tx = r.x() + r.width() - tw
        ty = r.y() + r.height() + 8
        if tx < 8:
            tx = 8
        if tx + tw > self.width() - 8:
            tx = self.width() - tw - 8
        if ty + th > self.height() - 8:
            ty = r.y() - th - 8
        if ty < 8:
            ty = max(8, r.y() + r.height() - th - 8)
        self.toolbar.move(int(tx * scale), int(ty * scale))
        self.toolbar.resize(self.toolbar.sizeHint())

    def _add_text(self, local: QPoint) -> None:
        text, ok = QInputDialog.getText(self, "文字", "输入标注文字：")
        if ok and text:
            self._strokes.append(Stroke("text", QColor(self._color), self._width, [local], text=text))
            self._redo.clear()
            self.update()

    def _undo(self) -> None:
        if self._strokes:
            self._redo.append(self._strokes.pop())
            self.update()

    def _redo_stroke(self) -> None:
        if self._redo:
            self._strokes.append(self._redo.pop())
            self.update()

    def _export_image(self) -> QImage:
        return self._crop_image()

    def _export_pixmap(self) -> QPixmap:
        return QPixmap.fromImage(self._export_image())

    def capture_full(self) -> None:
        self.session.clear_others(self)
        self._rect = self.rect()
        self._copy()

    def _copy(self) -> None:
        if self._norm_rect().width() <= 4:
            self._rect = self.rect()
        image = self._export_image()
        QApplication.clipboard().setImage(image)
        self.accepted.emit(None)

    def _save(self) -> None:
        if self._norm_rect().width() <= 4:
            self._rect = self.rect()
        default = pictures_dir() / "Screenshot.png"
        path, _ = QFileDialog.getSaveFileName(self, "保存截图", str(default), "PNG (*.png);;JPEG (*.jpg)")
        if not path:
            return
        image = self._export_image()
        writer = QImageWriter(path)
        if path.lower().endswith(".jpg") or path.lower().endswith(".jpeg"):
            writer.setQuality(95)
        else:
            writer.setQuality(80)
        if not writer.write(image):
            image.save(path)
        self.accepted.emit(None)

    def _pin(self) -> None:
        if self._norm_rect().width() <= 4:
            self._rect = self.rect()
        image = self._export_image()
        QApplication.clipboard().setImage(image)
        pix = QPixmap.fromImage(image)
        pin = PinWindow(pix)
        pin.move(self.mapToGlobal(self._norm_rect().topLeft()))
        self.accepted.emit(pin)

    def _cancel(self) -> None:
        self.cancelled.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self._cancel()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._copy()
        elif event.key() == Qt.Key_C and event.modifiers() & Qt.ControlModifier:
            self._copy()
        elif event.key() == Qt.Key_S and event.modifiers() & Qt.ControlModifier:
            self._save()
        elif event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            if event.modifiers() & Qt.ShiftModifier:
                self._redo_stroke()
            else:
                self._undo()
        elif event.key() == Qt.Key_Y and event.modifiers() & Qt.ControlModifier:
            self._redo_stroke()
        elif Qt.Key_1 <= event.key() <= Qt.Key_9:
            other = self.session.overlay_by_index(event.key() - Qt.Key_1)
            if other is not None:
                other.capture_full()
        else:
            super().keyPressEvent(event)
