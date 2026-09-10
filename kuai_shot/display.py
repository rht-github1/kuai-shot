from __future__ import annotations

from dataclasses import dataclass, field

from PyQt5.QtCore import QPoint, QRect, QSize, Qt
from PyQt5.QtGui import QColor, QCursor, QGuiApplication, QImage, QPixmap, QScreen


@dataclass
class ScreenShot:
    screen: QScreen
    image: QImage
    geometry: QRect
    name: str = ""

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
    mode: QSize = field(default_factory=lambda: QSize(0, 0))

    @property
    def physical_size(self) -> QSize:
        if self.mode.width() > 0 and self.mode.height() > 0:
            return QSize(self.mode.width(), self.mode.height())
        return QSize(
            max(1, int(round(self.logical.width() * self.scale))),
            max(1, int(round(self.logical.height() * self.scale))),
        )


@dataclass(frozen=True)
class LayoutCandidate:
    name: str
    canvas: QSize
    crops: tuple[QRect, ...]
    score: float = 0.0


def _qt_screens() -> dict[str, QScreen]:
    found: dict[str, QScreen] = {}
    for screen in QGuiApplication.screens() or []:
        name = (screen.name() or "").strip()
        if name:
            found[name] = screen
    return found


def _gdk_outputs() -> list[tuple[str, QRect, float]]:
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
    except Exception:
        return []
    display = Gdk.Display.get_default()
    if display is None:
        return []
    items: list[tuple[str, QRect, float]] = []
    for i in range(display.get_n_monitors()):
        mon = display.get_monitor(i)
        geo = mon.get_geometry()
        rect = QRect(geo.x, geo.y, geo.width, geo.height)
        scale = float(mon.get_scale_factor() or 1)
        model = (mon.get_model() or "").strip()
        items.append((model, rect, scale))
    return items


def _mutter_outputs() -> list[dict]:
    try:
        import os

        from gi.repository import Gio
    except Exception:
        return []
    try:
        addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS") or Gio.dbus_address_get_for_bus_sync(
            Gio.BusType.SESSION, None
        )
        conn = Gio.DBusConnection.new_for_address_sync(
            addr,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        proxy = Gio.DBusProxy.new_sync(
            conn,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.gnome.Mutter.DisplayConfig",
            "/org/gnome/Mutter/DisplayConfig",
            "org.gnome.Mutter.DisplayConfig",
            None,
        )
        _serial, monitors, logical, _props = proxy.call_sync(
            "GetCurrentState", None, Gio.DBusCallFlags.NONE, 2500, None
        ).unpack()
    except Exception:
        return []

    modes_by_name: dict[str, QSize] = {}
    for mon in monitors:
        try:
            spec = mon[0]
            connector = str(spec[0] if isinstance(spec, (tuple, list)) else spec)
            modes = mon[1] if len(mon) > 1 else []
            current = QSize(0, 0)
            for mode in modes:
                width, height = int(mode[1]), int(mode[2])
                props = mode[-1] if isinstance(mode[-1], dict) else {}
                if props.get("is-current"):
                    current = QSize(width, height)
                    break
                if current.width() == 0 and props.get("is-preferred"):
                    current = QSize(width, height)
            if current.width() == 0 and modes:
                current = QSize(int(modes[0][1]), int(modes[0][2]))
            if connector:
                modes_by_name[connector] = current
        except Exception:
            continue

    outputs: list[dict] = []
    for item in logical:
        try:
            x, y = int(item[0]), int(item[1])
            scale = float(item[2] or 1)
            for mon in item[5]:
                connector = str(mon[0])
                if not connector:
                    continue
                outputs.append(
                    {
                        "name": connector,
                        "x": x,
                        "y": y,
                        "scale": max(0.5, scale),
                        "mode": modes_by_name.get(connector, QSize(0, 0)),
                    }
                )
        except Exception:
            continue
    return outputs


def placeholder_shots(monitors: list[Monitor] | None = None) -> list[ScreenShot]:
    monitors = monitors or probe_monitors()
    shots: list[ScreenShot] = []
    for mon in monitors:
        image = QImage(
            max(8, mon.logical.width()),
            max(8, mon.logical.height()),
            QImage.Format_RGB32,
        )
        image.fill(QColor(18, 20, 24))
        shots.append(ScreenShot(mon.screen, image, QRect(mon.logical), mon.name))
    return shots


def probe_monitors() -> list[Monitor]:
    qt = _qt_screens()
    gdk = _gdk_outputs()
    mutter = _mutter_outputs()
    monitors: list[Monitor] = []

    if mutter:
        used_screens: set[int] = set()
        for item in mutter:
            screen = qt.get(item["name"])
            if screen is None:
                for other in qt.values():
                    if id(other) in used_screens:
                        continue
                    g = other.geometry()
                    if abs(g.x() - item["x"]) < 8 and abs(g.y() - item["y"]) < 8:
                        screen = other
                        break
            if screen is None:
                leftover = [s for s in qt.values() if id(s) not in used_screens]
                screen = leftover[0] if leftover else QGuiApplication.primaryScreen()
            if screen is None:
                continue
            used_screens.add(id(screen))
            geo = QRect(screen.geometry())
            logical = QRect(item["x"], item["y"], geo.width(), geo.height())
            if logical.width() < 8 or logical.height() < 8:
                logical = geo
            monitors.append(
                Monitor(
                    screen=screen,
                    name=item["name"] or screen.name() or f"screen-{len(monitors)}",
                    logical=logical if logical.width() > 0 else geo,
                    scale=item["scale"],
                    mode=item["mode"],
                )
            )
    else:
        for name, screen in qt.items():
            geo = QRect(screen.geometry())
            scale = float(screen.devicePixelRatio() or 1)
            for model, rect, gdk_scale in gdk:
                if abs(rect.x() - geo.x()) + abs(rect.y() - geo.y()) < 16:
                    scale = gdk_scale
                    break
                if model and model == name:
                    scale = gdk_scale
                    break
            monitors.append(
                Monitor(
                    screen=screen,
                    name=name or f"screen-{len(monitors)}",
                    logical=geo,
                    scale=max(0.5, scale),
                    mode=QSize(0, 0),
                )
            )

    if not monitors:
        for i, screen in enumerate(QGuiApplication.screens() or []):
            geo = QRect(screen.geometry())
            monitors.append(
                Monitor(
                    screen=screen,
                    name=screen.name() or f"screen-{i}",
                    logical=geo,
                    scale=max(0.5, float(screen.devicePixelRatio() or 1)),
                )
            )
    monitors.sort(key=lambda m: (m.logical.y(), m.logical.x()))
    return monitors


def monitor_names_for_rect(rect: QRect, monitors: list[Monitor] | None = None) -> list[str]:
    names: list[str] = []
    for mon in monitors or probe_monitors():
        if not mon.name or not mon.logical.intersects(rect):
            continue
        if mon.name not in names:
            names.append(mon.name)
    return names


def _bounds(rects: list[QRect]) -> QRect:
    box = QRect(rects[0])
    for rect in rects[1:]:
        box = box.united(rect)
    return box


def _crops_from_rects(rects: list[QRect]) -> tuple[QSize, tuple[QRect, ...]]:
    box = _bounds(rects)
    crops = tuple(QRect(r.x() - box.x(), r.y() - box.y(), r.width(), r.height()) for r in rects)
    return box.size(), crops


def _score(canvas: QSize, image: QSize) -> float:
    if canvas.width() <= 0 or canvas.height() <= 0 or image.width() <= 0 or image.height() <= 0:
        return 1e9
    dw = abs(canvas.width() - image.width()) / image.width()
    dh = abs(canvas.height() - image.height()) / image.height()
    ar_c = canvas.width() / canvas.height()
    ar_i = image.width() / image.height()
    return dw + dh + abs(ar_c - ar_i) * 0.25


def _candidates(monitors: list[Monitor]) -> list[LayoutCandidate]:
    image_placeholder = QSize(1, 1)
    variants: list[tuple[str, list[QRect]]] = [
        ("logical", [QRect(m.logical) for m in monitors]),
        (
            "mode-at-logical",
            [
                QRect(m.logical.x(), m.logical.y(), m.physical_size.width(), m.physical_size.height())
                for m in monitors
            ],
        ),
        (
            "mode-scaled-pos",
            [
                QRect(
                    int(round(m.logical.x() * m.scale)),
                    int(round(m.logical.y() * m.scale)),
                    m.physical_size.width(),
                    m.physical_size.height(),
                )
                for m in monitors
            ],
        ),
        (
            "logical-times-scale",
            [
                QRect(
                    m.logical.x(),
                    m.logical.y(),
                    max(1, int(round(m.logical.width() * m.scale))),
                    max(1, int(round(m.logical.height() * m.scale))),
                )
                for m in monitors
            ],
        ),
    ]
    items: list[LayoutCandidate] = []
    seen: set[tuple[int, int, tuple[tuple[int, int, int, int], ...]]] = set()
    for name, rects in variants:
        size, crops = _crops_from_rects(rects)
        key = (size.width(), size.height(), tuple((c.x(), c.y(), c.width(), c.height()) for c in crops))
        if key in seen:
            continue
        seen.add(key)
        items.append(LayoutCandidate(name, size, crops, _score(size, image_placeholder)))
    return items


def best_layout(full: QImage, monitors: list[Monitor]) -> LayoutCandidate:
    image = QSize(full.width(), full.height())
    scored = []
    for cand in _candidates(monitors):
        scored.append(LayoutCandidate(cand.name, cand.canvas, cand.crops, _score(cand.canvas, image)))
    return min(scored, key=lambda c: c.score)


def layout_fit(full: QImage, monitors: list[Monitor] | None = None) -> float:
    monitors = monitors or probe_monitors()
    if not monitors or full.isNull():
        return 1e9
    return best_layout(full, monitors).score


def split_by_screens(full: QImage, monitors: list[Monitor] | None = None) -> list[ScreenShot]:
    """Crop a captured canvas using the live output graph.

    There is no single-screen / multi-screen product path. One output
    or eight: the compositor is probed now, then this frame is scored
    against layout hypotheses. A poor score returns nothing so the
    caller can grab each output separately instead of stretching.
    """
    monitors = monitors or probe_monitors()
    if not monitors or full.isNull():
        return []
    best = best_layout(full, monitors)
    if best.score > 0.15:
        return []
    sx = full.width() / max(1, best.canvas.width())
    sy = full.height() / max(1, best.canvas.height())
    if abs(sx - 1.0) < 0.02 and abs(sy - 1.0) < 0.02:
        sx = sy = 1.0
    shots = []
    for mon, crop in zip(monitors, best.crops):
        src = QRect(
            int(round(crop.x() * sx)),
            int(round(crop.y() * sy)),
            max(1, int(round(crop.width() * sx))),
            max(1, int(round(crop.height() * sy))),
        ).intersected(QRect(0, 0, full.width(), full.height()))
        if src.width() < 8 or src.height() < 8:
            continue
        piece = full.copy(src)
        if not piece.isNull():
            shots.append(ScreenShot(mon.screen, piece, mon.logical, mon.name))
    if len(shots) != len(monitors):
        return []
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
    return split_by_screens(full, monitors)


def ui_scale_for(size: QSize, dpr: float) -> float:
    short = min(size.width(), size.height())
    by_size = short / 1080
    return max(0.8, min(1.5, by_size * max(0.75, min(1.15, 1.0 / max(0.5, dpr / 1.5)))))


def fit_pixmap(pix: QPixmap, max_size: QSize) -> QPixmap:
    if pix.width() <= max_size.width() and pix.height() <= max_size.height():
        return pix
    return pix.scaled(max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
