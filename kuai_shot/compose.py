from __future__ import annotations

from PyQt5.QtCore import QRect
from PyQt5.QtGui import QImage, QPainter

from .capture import capture_shots, shots_from_frames
from .display import ScreenShot, probe_monitors


def compose_pieces(pieces: list[tuple[QRect, QImage]], box: QRect) -> QImage | None:
    if not pieces or box.width() < 2 or box.height() < 2:
        return None
    canvas = QImage(max(1, box.width()), max(1, box.height()), QImage.Format_ARGB32)
    canvas.fill(0)
    painter = QPainter(canvas)
    painted = False
    for inter, image in pieces:
        if inter.isEmpty() or image.isNull():
            continue
        dest = QRect(inter.x() - box.x(), inter.y() - box.y(), inter.width(), inter.height())
        if dest.width() < 1 or dest.height() < 1:
            continue
        piece = image
        if piece.width() != dest.width() or piece.height() != dest.height():
            piece = piece.scaled(dest.width(), dest.height())
        painter.drawImage(dest.topLeft(), piece)
        painted = True
    painter.end()
    return canvas if painted else None


def compose_shots(shots: list[ScreenShot], box: QRect) -> QImage | None:
    if not shots or box.width() < 2 or box.height() < 2:
        return None
    pieces: list[tuple[QRect, QImage]] = []
    for shot in shots:
        geo = QRect(shot.geometry)
        inter = geo.intersected(box)
        if inter.isEmpty() or shot.image.isNull():
            continue
        sx = shot.image.width() / max(1, geo.width())
        sy = shot.image.height() / max(1, geo.height())
        src = QRect(
            int(round((inter.x() - geo.x()) * sx)),
            int(round((inter.y() - geo.y()) * sy)),
            max(1, int(round(inter.width() * sx))),
            max(1, int(round(inter.height() * sy))),
        ).intersected(QRect(0, 0, shot.image.width(), shot.image.height()))
        piece = shot.image.copy(src)
        if piece.isNull():
            continue
        pieces.append((inter, piece))
    return compose_pieces(pieces, box)


def compose_monitor_frames(frames: list[dict], box: QRect, monitors: list | None = None) -> QImage | None:
    monitors = monitors or probe_monitors()
    shots = shots_from_frames(frames, monitors)
    return compose_shots(shots, box)


def capture_desktop_rect(box: QRect, skip_portal: bool = False) -> QImage | None:
    monitors = probe_monitors()
    shots = capture_shots(monitors, skip_portal=skip_portal)
    return compose_shots(shots, box)
