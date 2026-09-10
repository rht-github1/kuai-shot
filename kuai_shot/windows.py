from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import QRect

_SKIP_TITLES = ("desktop icons", "kuai-shot", "快截图")
_SKIP_APPS = ("kuai-shot", "gnome-shell", "gjs")
_ATSPI_READY = False
_EDGE = 80


@dataclass(frozen=True)
class TopWindow:
    title: str
    app: str
    rect: QRect
    raw: QRect | None = None


def _init_atspi() -> bool:
    global _ATSPI_READY
    if _ATSPI_READY:
        return True
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        Atspi.init()
        _ATSPI_READY = True
        return True
    except Exception:
        return False


def _overlap_frac(rect: QRect, mon: QRect) -> float:
    inter = rect.intersected(mon)
    area = rect.width() * rect.height()
    if area <= 0:
        return 0.0
    return (inter.width() * inter.height()) / area


def _insets(rect: QRect, mon: QRect) -> tuple[int, int, int, int]:
    return (
        rect.x() - mon.x(),
        rect.y() - mon.y(),
        mon.x() + mon.width() - (rect.x() + rect.width()),
        mon.y() + mon.height() - (rect.y() + rect.height()),
    )


def _fills_monitor(rect: QRect, mon: QRect, edge: int = _EDGE) -> bool:
    if mon.width() < 8 or mon.height() < 8:
        return False
    if rect.width() > mon.width() + edge or rect.height() > mon.height() + edge:
        return False
    if rect.width() >= mon.width() - edge and rect.height() >= mon.height() - edge:
        left, top, right, bottom = _insets(rect, mon)
        if all(-edge <= v <= edge * 2 for v in (left, top, right, bottom)):
            return True
        if abs(rect.x() - mon.x()) <= edge and abs(rect.y() - mon.y()) <= edge:
            return True
    return False


def _snap_if_max(rect: QRect, mon: QRect) -> QRect:
    if _fills_monitor(rect, mon):
        return QRect(mon)
    placed = rect.intersected(mon)
    return placed if placed.width() >= 80 and placed.height() >= 80 else QRect(rect)


def candidate_rects(raw: QRect, monitors: list[QRect]) -> list[QRect]:
    """Map a compositor/AT-SPI frame onto live outputs.

    Wayland often reports (0,0) or a client box without chrome. A size that
    nearly fills one output is placed on that output; a real overlap is kept.
    """
    if not monitors or raw.width() < 160 or raw.height() < 120:
        return []
    max_w = max(m.width() for m in monitors)
    max_h = max(m.height() for m in monitors)
    if raw.width() > max_w + _EDGE * 2 or raw.height() > max_h + _EDGE * 2:
        return []

    found: list[QRect] = []
    seen: set[tuple[int, int, int, int]] = set()

    def add(rect: QRect) -> None:
        if rect.width() < 80 or rect.height() < 80:
            return
        key = (rect.x(), rect.y(), rect.width(), rect.height())
        if key in seen:
            return
        seen.add(key)
        found.append(QRect(rect))

    originish = abs(raw.x()) < 48 and abs(raw.y()) < 48
    if not originish:
        for mon in monitors:
            if _overlap_frac(raw, mon) >= 0.45:
                add(_snap_if_max(raw, mon))

    if originish or not found:
        for mon in monitors:
            placed = QRect(mon.x(), mon.y(), raw.width(), raw.height())
            if _fills_monitor(placed, mon):
                add(_snap_if_max(placed, mon))

    return found


def list_visible_windows(
    bounds: QRect | None = None,
    monitors: list[QRect] | None = None,
) -> list[TopWindow]:
    if not _init_atspi():
        return []
    try:
        from gi.repository import Atspi
    except Exception:
        return []
    try:
        desktop = Atspi.get_desktop(0)
    except Exception:
        return []
    found: list[TopWindow] = []
    seen: set[tuple[int, int, int, int]] = set()
    try:
        n_app = desktop.get_child_count()
    except Exception:
        return []
    for i in range(n_app):
        try:
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            app_name = (app.get_name() or "").strip()
        except Exception:
            continue
        if app_name.lower() in _SKIP_APPS:
            continue
        try:
            n_win = app.get_child_count()
        except Exception:
            continue
        for j in range(min(n_win, 40)):
            try:
                child = app.get_child_at_index(j)
                if child is None:
                    continue
                role = (child.get_role_name() or "").lower()
                title = (child.get_name() or "").strip()
            except Exception:
                continue
            if role not in {"frame", "window", "dialog"}:
                continue
            if any(skip in title.lower() for skip in _SKIP_TITLES):
                continue
            try:
                comp = child.get_component()
                if comp is None:
                    continue
                ext = comp.get_extents(Atspi.CoordType.SCREEN)
            except Exception:
                continue
            raw = QRect(int(ext.x), int(ext.y), int(ext.width), int(ext.height))
            if raw.width() < 160 or raw.height() < 120:
                continue
            if monitors:
                rects = candidate_rects(raw, monitors)
            elif bounds is not None:
                rects = [raw] if _overlap_frac(raw, bounds) >= 0.35 else []
            else:
                rects = [raw]
            for rect in rects:
                key = (rect.x(), rect.y(), rect.width(), rect.height())
                if key in seen:
                    continue
                seen.add(key)
                found.append(TopWindow(title=title or app_name, app=app_name, rect=rect, raw=raw))
    found.sort(key=lambda w: w.rect.width() * w.rect.height())
    return found


def window_at(
    pos_x: float,
    pos_y: float,
    windows: list[TopWindow] | None = None,
    monitors: list[QRect] | None = None,
) -> TopWindow | None:
    windows = windows if windows is not None else list_visible_windows(monitors=monitors)
    x, y = int(pos_x), int(pos_y)
    hits: list[TopWindow] = []
    for win in windows:
        rects = candidate_rects(win.raw or win.rect, monitors) if monitors else [win.rect]
        for rect in rects:
            if rect.contains(x, y):
                hits.append(TopWindow(win.title, win.app, rect, win.raw))
                break
    if not hits:
        return None
    return min(hits, key=lambda w: w.rect.width() * w.rect.height())


def scroll_at(desk_x: float, desk_y: float, dx: float, dy: float) -> bool:
    """Inject a wheel/page scroll at a desktop point. Call after the overlay is hidden."""
    if not _init_atspi():
        return False
    try:
        from gi.repository import Atspi
    except Exception:
        return False
    x, y = int(round(desk_x)), int(round(desk_y))
    sent = False
    try:
        Atspi.generate_mouse_event(x, y, "abs")
        sent = True
    except Exception:
        pass
    clicks_y = max(1, min(8, int(round(abs(dy))) or (1 if dy else 0)))
    clicks_x = max(0, min(8, int(round(abs(dx)))))
    btn_y = "b5" if dy > 0 else "b4"
    btn_x = "b7" if dx > 0 else "b6"
    for name, n in ((btn_y, clicks_y if dy else 0), (btn_x, clicks_x)):
        for _ in range(n):
            try:
                Atspi.generate_mouse_event(x, y, name)
                sent = True
            except Exception:
                pass
    if abs(dy) >= 0.2:
        key = "Page_Down" if dy > 0 else "Page_Up"
        try:
            Atspi.generate_keyboard_event(0, key, Atspi.KeySynthType.STRING)
            sent = True
        except Exception:
            try:
                Atspi.generate_keyboard_event(0xFF56 if dy > 0 else 0xFF55, "", Atspi.KeySynthType.SYM)
                sent = True
            except Exception:
                pass
    return sent
