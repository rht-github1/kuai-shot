from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import QPoint, QRect, QSize
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QCursor, QGuiApplication, QImage, QPixmap, QScreen


@dataclass
class ScreenShot:
    screen: QScreen
    image: QImage
    geometry: QRect

    def pixmap(self) -> QPixmap:
        pix = QPixmap.fromImage(self.image)
        pix.setDevicePixelRatio(1.0)
        return pix


@dataclass(frozen=True)
class Monitor:
    screen: QScreen
    name: str
    logical: QRect
    scale: float

    @property
    def physical_size(self) -> QSize:
        return QSize(
            max(1, int(round(self.logical.width() * self.scale))),
            max(1, int(round(self.logical.height() * self.scale))),
        )


@dataclass(frozen=True)
class LayoutCandidate:
    name: str
    canvas: QSize
    crops: tuple[QRect, ...]


def _gdk_scales() -> dict[str, float]:
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        if display is None:
            return {}
        scales: dict[str, float] = {}
        for i in range(display.get_n_monitors()):
            mon = display.get_monitor(i)
            geo = mon.get_geometry()
            key = f"{geo.x},{geo.y},{geo.width}x{geo.height}"
            scales[key] = float(mon.get_scale_factor() or 1)
            model = (mon.get_model() or "").strip()
            if model:
                scales[model] = float(mon.get_scale_factor() or 1)
        return scales
    except Exception:
        return {}


def probe_monitors() -> list[Monitor]:
    screens = list(QGuiApplication.screens() or [])
    gdk = _gdk_scales()
    monitors: list[Monitor] = []
    for screen in screens:
        geo = QRect(screen.geometry())
        key = f"{geo.x()},{geo.y()},{geo.width()}x{geo.height()}"
        scale = gdk.get(key) or gdk.get(screen.name(), 0) or float(screen.devicePixelRatio() or 1)
        monitors.append(
            Monitor(
                screen=screen,
                name=screen.name() or f"screen-{len(monitors)}",
                logical=geo,
                scale=max(0.5, float(scale)),
            )
        )
    monitors.sort(key=lambda m: (m.logical.y(), m.logical.x()))
    return monitors


def _bounds(rects: list[QRect]) -> QRect:
    box = QRect(rects[0])
    for rect in rects[1:]:
        box = box.united(rect)
    return box


def _crops_from_rects(rects: list[QRect]) -> tuple[QSize, tuple[QRect, ...]]:
    box = _bounds(rects)
    crops = tuple(QRect(r.x() - box.x(), r.y() - box.y(), r.width(), r.height()) for r in rects)
    return box.size(), crops


def _candidates(monitors: list[Monitor]) -> list[LayoutCandidate]:
    logical = [m.logical for m in monitors]
    items: list[LayoutCandidate] = []

    size, crops = _crops_from_rects(logical)
    items.append(LayoutCandidate("logical", size, crops))

    physical_at_logical_pos = [
        QRect(m.logical.x(), m.logical.y(), m.physical_size.width(), m.physical_size.height())
        for m in monitors
    ]
    size, crops = _crops_from_rects(physical_at_logical_pos)
    items.append(LayoutCandidate("physical-size", size, crops))

    physical_scaled_pos = [
        QRect(
            int(round(m.logical.x() * m.scale)),
            int(round(m.logical.y() * m.scale)),
            m.physical_size.width(),
            m.physical_size.height(),
        )
        for m in monitors
    ]
    size, crops = _crops_from_rects(physical_scaled_pos)
    items.append(LayoutCandidate("physical-pos", size, crops))

    common = monitors[0].scale
    if all(abs(m.scale - common) < 0.01 for m in monitors):
        scaled = [
            QRect(
                int(round(m.logical.x() * common)),
                int(round(m.logical.y() * common)),
                int(round(m.logical.width() * common)),
                int(round(m.logical.height() * common)),
            )
            for m in monitors
        ]
        size, crops = _crops_from_rects(scaled)
        items.append(LayoutCandidate("uniform-scale", size, crops))
    return items


def _score(canvas: QSize, image: QSize) -> float:
    if canvas.width() <= 0 or canvas.height() <= 0:
        return 1e9
    dw = abs(canvas.width() - image.width()) / image.width()
    dh = abs(canvas.height() - image.height()) / image.height()
    ar_c = canvas.width() / canvas.height()
    ar_i = image.width() / max(1, image.height())
    return dw + dh + abs(ar_c - ar_i) * 0.25


def _match_single_monitor(full: QImage, monitors: list[Monitor]) -> ScreenShot | None:
    image = QSize(full.width(), full.height())
    for mon in monitors:
        for size in (mon.logical.size(), mon.physical_size):
            if _score(size, image) < 0.02:
                return ScreenShot(screen=mon.screen, image=full, geometry=mon.logical)
    return None


def split_by_screens(full: QImage, monitors: list[Monitor] | None = None) -> list[ScreenShot]:
    monitors = monitors or probe_monitors()
    if not monitors or full.isNull():
        return []
    if len(monitors) == 1:
        return [ScreenShot(monitors[0].screen, full, monitors[0].logical)]

    virtual = _bounds([m.logical for m in monitors]).size()
    image = QSize(full.width(), full.height())
    if _score(virtual, image) > 0.2:
        single = _match_single_monitor(full, monitors)
        if single is not None:
            return [single]

    best = min(_candidates(monitors), key=lambda c: _score(c.canvas, image))
    if _score(best.canvas, image) > 0.08:
        logical = [m.logical for m in monitors]
        _, crops = _crops_from_rects(logical)
        sx = full.width() / max(1, _bounds(logical).width())
        sy = full.height() / max(1, _bounds(logical).height())
        shots = []
        for mon, crop in zip(monitors, crops):
            src = QRect(
                int(round(crop.x() * sx)),
                int(round(crop.y() * sy)),
                max(1, int(round(crop.width() * sx))),
                max(1, int(round(crop.height() * sy))),
            )
            piece = full.copy(src.intersected(QRect(0, 0, full.width(), full.height())))
            if not piece.isNull():
                shots.append(ScreenShot(mon.screen, piece, mon.logical))
        return shots

    shots = []
    for mon, crop in zip(monitors, best.crops):
        src = crop.intersected(QRect(0, 0, full.width(), full.height()))
        piece = full.copy(src)
        if piece.isNull():
            continue
        shots.append(ScreenShot(mon.screen, piece, mon.logical))
    return shots


def cursor_pos() -> QPoint:
    return QCursor.pos()


def monitor_at(pos: QPoint, monitors: list[Monitor] | None = None) -> Monitor | None:
    monitors = monitors or probe_monitors()
    if not monitors:
        return None
    for mon in monitors:
        if mon.logical.contains(pos):
            return mon

    def dist2(mon: Monitor) -> int:
        rect = mon.logical
        x = min(max(pos.x(), rect.left()), rect.right())
        y = min(max(pos.y(), rect.top()), rect.bottom())
        return (pos.x() - x) ** 2 + (pos.y() - y) ** 2

    return min(monitors, key=dist2)


def screen_under_cursor(monitors: list[Monitor] | None = None) -> QScreen | None:
    monitors = monitors or probe_monitors()
    mon = monitor_at(QCursor.pos(), monitors)
    if mon is not None:
        return mon.screen
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def split_screen_under_cursor(
    full: QImage,
    monitors: list[Monitor] | None = None,
    pos: QPoint | None = None,
) -> list[ScreenShot]:
    monitors = monitors or probe_monitors()
    pos = pos or QCursor.pos()
    shots = split_by_screens(full, monitors)
    if not shots:
        return []
    mon = monitor_at(pos, monitors)
    if mon is None:
        return shots[:1]
    for shot in shots:
        if shot.screen == mon.screen or shot.geometry == mon.logical:
            return [shot]
    return shots[:1]


def ui_scale_for(size: QSize, dpr: float) -> float:
    short = min(size.width(), size.height())
    by_size = short / 1080
    return max(0.8, min(1.5, by_size * max(0.75, min(1.15, 1.0 / max(0.5, dpr / 1.5)))))


def fit_pixmap(pix: QPixmap, max_size: QSize) -> QPixmap:
    if pix.width() <= max_size.width() and pix.height() <= max_size.height():
        return pix
    return pix.scaled(max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
