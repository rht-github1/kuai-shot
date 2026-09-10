from __future__ import annotations

import math
import threading
from datetime import datetime
from dataclasses import dataclass, field

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
from PyQt5.QtCore import QObject, QRect, pyqtSignal
from PyQt5.QtGui import QImage, QPainter

from .display import ScreenShot
from .paths import pictures_dir
from .windows import TopWindow, _init_atspi, list_visible_windows, window_at

_GTK_READY = False
TOOLS = ["rect", "ellipse", "arrow", "pen", "highlight", "mosaic", "text"]
BAR_KINDS = TOOLS + ["undo", "redo", "save", "pin", "ok", "cancel"]
HANDLE_NAMES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
HANDLE_CURSORS = {
    "nw": "nw-resize",
    "se": "se-resize",
    "ne": "ne-resize",
    "sw": "sw-resize",
    "n": "n-resize",
    "s": "s-resize",
    "e": "e-resize",
    "w": "w-resize",
    "move": "grabbing",
}
COLORS = [
    (245, 74, 69),
    (255, 141, 26),
    (255, 198, 10),
    (52, 199, 36),
    (51, 112, 255),
    (123, 103, 238),
    (255, 255, 255),
    (31, 35, 41),
]


def ensure_gtk() -> None:
    global _GTK_READY
    if _GTK_READY:
        return
    Gtk.init([])
    _GTK_READY = True


def pump_gtk() -> None:
    ctx = GLib.MainContext.default()
    n = 0
    while ctx.pending() and n < 128:
        ctx.iteration(False)
        n += 1


def qimage_to_pixbuf(image: QImage) -> GdkPixbuf.Pixbuf:
    if image.format() != QImage.Format_RGBA8888:
        image = image.convertToFormat(QImage.Format_RGBA8888)
    width, height = image.width(), image.height()
    stride = image.bytesPerLine()
    bits = image.constBits()
    raw = bits.asstring(stride * height) if hasattr(bits, "asstring") else bytes(bits)
    src = GdkPixbuf.Pixbuf.new_from_data(
        raw, GdkPixbuf.Colorspace.RGB, True, 8, width, height, stride
    )
    return src.copy()


def pixbuf_to_qimage(pixbuf: GdkPixbuf.Pixbuf) -> QImage:
    width, height = pixbuf.get_width(), pixbuf.get_height()
    stride = pixbuf.get_rowstride()
    raw = bytes(pixbuf.get_pixels())
    fmt = QImage.Format_RGBA8888 if pixbuf.get_has_alpha() else QImage.Format_RGB888
    return QImage(raw, width, height, stride, fmt).copy()


def monitor_index_for(
    geo: QRect, name: str = "", exclude: set[int] | None = None
) -> tuple[int, object]:
    display = Gdk.Display.get_default()
    best, best_d, best_mon = 0, 10**9, None
    n = display.get_n_monitors()
    skip = exclude or set()
    cx = geo.x() + geo.width() // 2
    cy = geo.y() + geo.height() // 2
    needle = (name or "").strip().lower()
    for i in range(n):
        if i in skip:
            continue
        mon = display.get_monitor(i)
        g = mon.get_geometry()
        d = abs(g.x - geo.x()) + abs(g.y - geo.y()) + abs(g.width - geo.width()) + abs(g.height - geo.height())
        if g.x <= cx < g.x + g.width and g.y <= cy < g.y + g.height:
            d -= 10000
        labels = " ".join(
            str(part or "")
            for part in (
                getattr(mon, "get_model", lambda: "")(),
                getattr(mon, "get_manufacturer", lambda: "")(),
                getattr(mon, "get_connector", lambda: "")(),
            )
        ).lower()
        if needle and needle in labels:
            d -= 5000
        if d < best_d:
            best, best_d, best_mon = i, d, mon
    return best, best_mon


@dataclass
class Stroke:
    kind: str
    color: tuple[int, int, int]
    width: int
    points: list[tuple[float, float]] = field(default_factory=list)
    text: str = ""


class PinWindow:
    def __init__(self, pixbuf: GdkPixbuf.Pixbuf):
        ensure_gtk()
        display = Gdk.Display.get_default()
        mon = display.get_monitor_at_point(0, 0) if display else None
        max_w, max_h = 1200, 800
        if mon is not None:
            g = mon.get_geometry()
            max_w = max(160, int(g.width * 0.8))
            max_h = max(120, int(g.height * 0.8))
        w, h = pixbuf.get_width(), pixbuf.get_height()
        if w > max_w or h > max_h:
            scale = min(max_w / max(1, w), max_h / max(1, h))
            pixbuf = pixbuf.scale_simple(
                max(1, int(w * scale)), max(1, int(h * scale)), GdkPixbuf.InterpType.BILINEAR
            )
        self.pixbuf = pixbuf
        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_decorated(False)
        self.win.set_keep_above(True)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_accept_focus(False)
        self.win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.win.set_default_size(pixbuf.get_width(), pixbuf.get_height())
        self.da = Gtk.DrawingArea()
        self.win.add(self.da)
        self.da.connect("draw", self._on_draw)
        self.win.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON_MOTION_MASK
        )
        self.win.connect("button-press-event", self._on_press)
        self.win.connect("button-release-event", self._on_release)
        self.win.show_all()
        self.win.present()

    def _on_draw(self, _w, cr):
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        cr.set_source_rgb(0.20, 0.44, 1)
        cr.set_line_width(2)
        cr.rectangle(0.5, 0.5, self.pixbuf.get_width() - 1, self.pixbuf.get_height() - 1)
        cr.stroke()
        return False

    def _on_press(self, _w, ev):
        if ev.button == 3 or ev.type == Gdk.EventType._2BUTTON_PRESS:
            self.win.destroy()
            return True
        if ev.button == 1:
            try:
                self.win.begin_move_drag(int(ev.button), int(ev.x_root), int(ev.y_root), int(ev.time))
            except Exception:
                pass
            return True
        return False

    def _on_release(self, *_a):
        return False


class GtkOverlay:
    def __init__(self, shot: ScreenShot, session: "OverlaySession"):
        ensure_gtk()
        self.session = session
        self.name = shot.name
        self.desk = QRect(shot.geometry)
        self.pixbuf = qimage_to_pixbuf(shot.image)
        self.img_w = self.pixbuf.get_width()
        self.img_h = self.pixbuf.get_height()
        self.tool = "rect"
        self.color = COLORS[0]
        self.pen_w = 4
        self.strokes: list[Stroke] = []
        self.redo: list[Stroke] = []
        self.draft: Stroke | None = None
        self.closed = False
        self._seat = None
        self._last_ev = None
        self._bar_slots = None
        self._color_slots = None

        screen = Gdk.Screen.get_default()
        idx, gdk_mon = monitor_index_for(shot.geometry, shot.name, session._used_monitors)
        self.mon_geo = screen.get_monitor_geometry(idx)
        self.mon_idx = idx
        self.mon_sf = float(gdk_mon.get_scale_factor() if gdk_mon is not None else 1)

        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_decorated(False)
        self.win.set_keep_above(True)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_accept_focus(True)
        self.win.set_resizable(True)
        self.win.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        mask = (
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.SCROLL_MASK
        )
        self.da = Gtk.DrawingArea()
        self.da.set_can_focus(True)
        self.da.set_hexpand(True)
        self.da.set_vexpand(True)
        self.win.add(self.da)
        self.win.add_events(mask)
        self.da.add_events(mask)
        self.da.connect("draw", self._on_draw)
        for widget in (self.win, self.da):
            widget.connect("button-press-event", self._on_press)
            widget.connect("button-release-event", self._on_release)
            widget.connect("motion-notify-event", self._on_motion)
        self.win.connect("enter-notify-event", self._on_enter)
        self.win.connect("key-press-event", self._on_key)
        self.win.connect("delete-event", self._on_delete)
        self.win.connect("realize", self._on_realize)
        self.win.move(self.mon_geo.x, self.mon_geo.y)
        self.win.set_default_size(self.mon_geo.width, self.mon_geo.height)
        self.win.resize(self.mon_geo.width, self.mon_geo.height)
        self.win.fullscreen_on_monitor(screen, idx)
        self.win.show_all()
        GLib.idle_add(self._prep_window)

    def apply_shot(self, shot: ScreenShot) -> None:
        self.name = shot.name or self.name
        self.desk = QRect(shot.geometry)
        self.pixbuf = qimage_to_pixbuf(shot.image)
        self.img_w = self.pixbuf.get_width()
        self.img_h = self.pixbuf.get_height()
        self.da.queue_draw()

    def _on_realize(self, *_a) -> None:
        self.win.move(self.mon_geo.x, self.mon_geo.y)
        self.win.resize(self.mon_geo.width, self.mon_geo.height)

    def _win_sf(self) -> float:
        return max(1.0, float(self.win.get_scale_factor() or 1))

    def _view_size(self) -> tuple[float, float]:
        alloc = self.da.get_allocation()
        aw, ah = float(max(1, alloc.width)), float(max(1, alloc.height))
        ratio = self.mon_sf / self._win_sf()
        vis_w = min(aw, max(1.0, self.mon_geo.width * ratio))
        vis_h = min(ah, max(1.0, self.mon_geo.height * ratio))
        return vis_w, vis_h

    def _event_xy(self, widget, ev) -> tuple[float, float]:
        x, y = float(ev.x), float(ev.y)
        if widget is not self.da:
            try:
                ok, tx, ty = widget.translate_coordinates(self.da, int(x), int(y))
                if ok:
                    return float(tx), float(ty)
            except Exception:
                pass
        return x, y

    def _prep_window(self) -> bool:
        gdk_win = self.win.get_window()
        if gdk_win is None:
            return False
        gdk_win.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.SCROLL_MASK
        )
        self._set_cursor("crosshair")
        self.win.present()
        self.da.grab_focus()
        return False

    def _set_cursor(self, name: str) -> None:
        try:
            cursor = Gdk.Cursor.new_from_name(self.win.get_display(), name)
            self.win.get_window().set_cursor(cursor)
        except Exception:
            pass

    def _sx_sy(self) -> tuple[float, float]:
        vis_w, vis_h = self._view_size()
        return vis_w / max(1, self.img_w), vis_h / max(1, self.img_h)

    def _view_to_desk(self, vx: float, vy: float) -> tuple[float, float]:
        vis_w, vis_h = self._view_size()
        ix = vx * self.img_w / vis_w
        iy = vy * self.img_h / vis_h
        dx = self.desk.x() + ix * self.desk.width() / max(1, self.img_w)
        dy = self.desk.y() + iy * self.desk.height() / max(1, self.img_h)
        return dx, dy

    def _event_desk(self, widget, ev) -> tuple[float, float]:
        vx, vy = self._event_xy(widget, ev)
        return self._view_to_desk(vx, vy)

    def _desk_to_img(self, dx: float, dy: float) -> tuple[float, float]:
        ix = (dx - self.desk.x()) * self.img_w / max(1, self.desk.width())
        iy = (dy - self.desk.y()) * self.img_h / max(1, self.desk.height())
        return ix, iy

    def _local_rect(self, desk: QRect | None) -> tuple[float, float, float, float] | None:
        if desk is None or desk.width() < 1 or desk.height() < 1:
            return None
        inter = desk.intersected(self.desk)
        if inter.isEmpty():
            return None
        x1, y1 = self._desk_to_img(inter.x(), inter.y())
        x2, y2 = self._desk_to_img(inter.x() + inter.width(), inter.y() + inter.height())
        return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)

    def _on_draw(self, _w, cr):
        vis_w, vis_h = self._view_size()
        sx, sy = self._sx_sy()
        cr.save()
        cr.scale(sx, sy)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        if not self.session.ready:
            cr.set_source_rgba(0, 0, 0, 0.28)
            cr.rectangle(0, 0, self.img_w, self.img_h)
            cr.fill()
        hover = self._local_rect(self.session.hover_rect())
        if hover is not None and self.session.sel_rect() is None:
            hx, hy, hw, hh = hover
            cr.set_source_rgba(0.20, 0.55, 1.0, 0.16)
            cr.rectangle(hx, hy, hw, hh)
            cr.fill()
            cr.set_source_rgb(0.35, 0.65, 1.0)
            cr.set_dash([8, 6])
            cr.set_line_width(2)
            cr.rectangle(hx, hy, hw, hh)
            cr.stroke()
            cr.set_dash([])
        x, y, w, h = self._local_rect(self.session.sel_rect()) or (0, 0, 0, 0)
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.rectangle(0, 0, self.img_w, self.img_h)
        if w > 2 and h > 2:
            cr.rectangle(x, y, w, h)
            cr.set_fill_rule(1)
        cr.fill()
        if w > 2 and h > 2:
            cr.set_source_rgb(0.20, 0.44, 1)
            cr.set_line_width(2)
            cr.rectangle(x, y, w, h)
            cr.stroke()
            cr.set_source_rgb(1, 1, 1)
            for hx, hy in self._handle_points(x, y, w, h).values():
                cr.rectangle(hx - 4, hy - 4, 8, 8)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.select_font_face("Sans")
            cr.set_font_size(16)
            cr.move_to(x, max(18, y - 8))
            box = self.session.sel_rect()
            cr.show_text(f"{box.width()} × {box.height()}" if box else f"{int(w)} × {int(h)}")
        for stroke in self.strokes + ([self.draft] if self.draft else []):
            self._draw_stroke(cr, stroke)
        cr.restore()
        self._draw_hint(cr, vis_w)
        if w > 2 and self.session.toolbar_owner() is self:
            self._draw_bar(cr, vis_w, vis_h, sx, sy, x, y, w, h)
        else:
            self._bar_slots = None
            self._color_slots = None
        return False

    def _handle_points(self, x, y, w, h) -> dict[str, tuple[float, float]]:
        return {
            "nw": (x, y),
            "n": (x + w / 2, y),
            "ne": (x + w, y),
            "e": (x + w, y + h / 2),
            "se": (x + w, y + h),
            "s": (x + w / 2, y + h),
            "sw": (x, y + h),
            "w": (x, y + h / 2),
        }

    def _hit_handle(self, ix: float, iy: float) -> str:
        local = self._local_rect(self.session.sel_rect())
        if local is None:
            return ""
        x, y, w, h = local
        if w < 8:
            return ""
        for name, (hx, hy) in self._handle_points(x, y, w, h).items():
            if abs(ix - hx) <= 14 and abs(iy - hy) <= 14:
                return name
        if x <= ix <= x + w and y <= iy <= y + h:
            inset = 18
            if ix < x + inset or ix > x + w - inset or iy < y + inset or iy > y + h - inset:
                return "move"
            return "inside"
        return ""

    def _draw_hint(self, cr, win_w):
        if self is not self.session.overlays[0]:
            return
        text = "拖拽跨屏选区  ·  点窗口套住  ·  八向缩放  ·  Enter复制  ·  钉住不关文件"
        cr.set_source_rgba(0.17, 0.18, 0.21, 0.92)
        cr.rectangle(16, 16, min(win_w - 32, 640), 28)
        cr.fill()
        cr.set_source_rgb(0.91, 0.92, 0.93)
        cr.set_font_size(13)
        cr.move_to(24, 35)
        cr.show_text(text)

    def _round_rect(self, cr, x, y, w, h, r) -> None:
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, math.pi * 1.5)
        cr.close_path()

    def _draw_bar(self, cr, win_w, win_h, sx, sy, x, y, w, h):
        kinds = BAR_KINDS
        bw, bh, pad = 36, 40, 6
        seps = {7, 9}
        total = pad * 2 + len(kinds) * bw + len(seps) * 10
        px = min(win_w - total - 10, max(10, (x + w) * sx - total))
        py = min(win_h - bh - 28, max(10, (y + h) * sy + 10))
        self._round_rect(cr, px, py, total, bh, 12)
        cr.set_source_rgba(0.07, 0.09, 0.12, 0.94)
        cr.fill_preserve()
        cr.set_source_rgba(0.28, 0.34, 0.42, 0.55)
        cr.set_line_width(1)
        cr.stroke()
        cursor = px + pad
        slots = []
        for i, kind in enumerate(kinds):
            if i in seps:
                cr.set_source_rgba(1, 1, 1, 0.12)
                cr.set_line_width(1)
                cr.move_to(cursor + 5, py + 10)
                cr.line_to(cursor + 5, py + bh - 10)
                cr.stroke()
                cursor += 10
            slots.append((kind, cursor))
            active = kind == self.tool
            if active:
                self._round_rect(cr, cursor + 3, py + 4, bw - 6, bh - 8, 8)
                cr.set_source_rgba(0.18, 0.46, 1.0, 0.95)
                cr.fill()
            elif kind == "ok":
                self._round_rect(cr, cursor + 3, py + 4, bw - 6, bh - 8, 8)
                cr.set_source_rgba(0.13, 0.72, 0.47, 0.18)
                cr.fill()
            elif kind == "cancel":
                self._round_rect(cr, cursor + 3, py + 4, bw - 6, bh - 8, 8)
                cr.set_source_rgba(0.96, 0.32, 0.36, 0.16)
                cr.fill()
            self._draw_icon(cr, kind, cursor + (bw - 20) / 2, py + (bh - 20) / 2, active)
            cursor += bw
        self._bar_slots = (py, bh, slots, bw)
        cy = py + bh + 6
        cx = px
        color_slots = []
        for rgb in COLORS:
            cr.set_source_rgb(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
            cr.arc(cx + 8, cy + 8, 7, 0, math.tau)
            cr.fill()
            if rgb == self.color:
                cr.set_source_rgb(1, 1, 1)
                cr.set_line_width(1.6)
                cr.arc(cx + 8, cy + 8, 8.2, 0, math.tau)
                cr.stroke()
            color_slots.append((rgb, cx, cy))
            cx += 20
        self._color_slots = (cy, color_slots)

    def _draw_icon(self, cr, kind: str, x: float, y: float, active: bool = False) -> None:
        cr.save()
        cr.set_line_width(1.7)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        if kind == "ok":
            cr.set_source_rgb(0.35, 0.95, 0.62) if not active else cr.set_source_rgb(1, 1, 1)
        elif kind == "cancel":
            cr.set_source_rgb(1.0, 0.45, 0.48) if not active else cr.set_source_rgb(1, 1, 1)
        else:
            cr.set_source_rgb(1, 1, 1) if active else cr.set_source_rgb(0.86, 0.90, 0.94)
        if kind == "rect":
            self._round_rect(cr, x + 2, y + 3.5, 16, 13, 2.5)
            cr.stroke()
        elif kind == "ellipse":
            cr.arc(x + 10, y + 10, 7.2, 0, math.tau)
            cr.stroke()
        elif kind == "arrow":
            cr.move_to(x + 4, y + 16)
            cr.line_to(x + 14.5, y + 5.5)
            cr.stroke()
            cr.move_to(x + 8.2, y + 4.6)
            cr.line_to(x + 16.2, y + 4.2)
            cr.line_to(x + 15.6, y + 12.2)
            cr.close_path()
            cr.fill()
        elif kind == "pen":
            cr.move_to(x + 13.8, y + 2.6)
            cr.line_to(x + 17.2, y + 6.0)
            cr.line_to(x + 7.4, y + 15.8)
            cr.line_to(x + 3.2, y + 17.0)
            cr.line_to(x + 4.4, y + 12.8)
            cr.close_path()
            cr.stroke()
            cr.move_to(x + 6.2, y + 14.2)
            cr.line_to(x + 14.6, y + 5.8)
            cr.stroke()
        elif kind == "highlight":
            cr.set_source_rgba(1, 0.82, 0.16, 0.85 if active else 0.55)
            self._round_rect(cr, x + 1.5, y + 6, 17, 8, 2)
            cr.fill()
            cr.set_source_rgb(1, 1, 1) if active else cr.set_source_rgb(0.86, 0.90, 0.94)
            cr.move_to(x + 4, y + 14)
            cr.line_to(x + 15, y + 5)
            cr.stroke()
        elif kind == "mosaic":
            cells = ((0, 0, 0.95), (9, 0, 0.45), (0, 9, 0.45), (9, 9, 0.95))
            for cx, cy, a in cells:
                cr.set_source_rgba(0.86, 0.90, 0.94, a) if not active else cr.set_source_rgba(1, 1, 1, a)
                self._round_rect(cr, x + 2 + cx, y + 2 + cy, 7, 7, 1.6)
                cr.fill()
        elif kind == "text":
            cr.set_line_width(1.9)
            cr.move_to(x + 4, y + 4.2)
            cr.line_to(x + 16, y + 4.2)
            cr.move_to(x + 10, y + 4.2)
            cr.line_to(x + 10, y + 16.4)
            cr.stroke()
            cr.set_line_width(1.5)
            cr.move_to(x + 6.5, y + 16.4)
            cr.line_to(x + 13.5, y + 16.4)
            cr.stroke()
        elif kind == "undo":
            cr.arc_negative(x + 10.2, y + 10.6, 6.2, math.radians(28), math.radians(-145))
            cr.stroke()
            cr.move_to(x + 2.2, y + 6.4)
            cr.line_to(x + 2.0, y + 12.6)
            cr.line_to(x + 7.8, y + 11.4)
            cr.close_path()
            cr.fill()
        elif kind == "redo":
            cr.arc(x + 9.8, y + 10.6, 6.2, math.radians(152), math.radians(325))
            cr.stroke()
            cr.move_to(x + 17.8, y + 6.4)
            cr.line_to(x + 18.0, y + 12.6)
            cr.line_to(x + 12.2, y + 11.4)
            cr.close_path()
            cr.fill()
        elif kind == "save":
            cr.move_to(x + 10, y + 2.8)
            cr.line_to(x + 10, y + 12.2)
            cr.stroke()
            cr.move_to(x + 5.6, y + 8.4)
            cr.line_to(x + 10, y + 13.2)
            cr.line_to(x + 14.4, y + 8.4)
            cr.stroke()
            cr.move_to(x + 3.4, y + 15.6)
            cr.line_to(x + 16.6, y + 15.6)
            cr.stroke()
        elif kind == "pin":
            cr.move_to(x + 10, y + 3)
            cr.line_to(x + 14.5, y + 7.5)
            cr.line_to(x + 12.2, y + 9.8)
            cr.line_to(x + 8, y + 5.6)
            cr.close_path()
            cr.fill()
            cr.move_to(x + 9.2, y + 10.4)
            cr.line_to(x + 6.2, y + 16.6)
            cr.stroke()
        elif kind == "ok":
            cr.set_line_width(2.1)
            cr.move_to(x + 3.4, y + 10.4)
            cr.line_to(x + 8.2, y + 15.2)
            cr.line_to(x + 16.8, y + 5.0)
            cr.stroke()
        elif kind == "cancel":
            cr.set_line_width(2.0)
            cr.move_to(x + 4.4, y + 4.4)
            cr.line_to(x + 15.6, y + 15.6)
            cr.move_to(x + 15.6, y + 4.4)
            cr.line_to(x + 4.4, y + 15.6)
            cr.stroke()
        cr.restore()

    def _draw_stroke(self, cr, stroke: Stroke):
        if len(stroke.points) < 1:
            return
        r, g, b = [c / 255 for c in stroke.color]
        cr.set_source_rgb(r, g, b)
        cr.set_line_width(stroke.width)
        pts = stroke.points
        if stroke.kind == "pen" and len(pts) >= 2:
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            cr.stroke()
        elif stroke.kind in {"rect", "ellipse", "highlight"} and len(pts) >= 2:
            x1, y1 = pts[0]
            x2, y2 = pts[-1]
            box = (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
            if stroke.kind == "highlight":
                cr.set_source_rgba(r, g, b, 0.38)
                cr.rectangle(*box)
                cr.fill()
            elif stroke.kind == "rect":
                cr.rectangle(*box)
                cr.stroke()
            else:
                cr.save()
                cr.translate(box[0] + box[2] / 2, box[1] + box[3] / 2)
                cr.scale(max(box[2] / 2, 0.1), max(box[3] / 2, 0.1))
                cr.arc(0, 0, 1, 0, 6.283)
                cr.restore()
                cr.stroke()
        elif stroke.kind == "arrow" and len(pts) >= 2:
            x1, y1 = pts[0]
            x2, y2 = pts[-1]
            cr.move_to(x1, y1)
            cr.line_to(x2, y2)
            cr.stroke()
            ang = math.atan2(y2 - y1, x2 - x1)
            size = max(10, stroke.width * 3)
            cr.move_to(x2, y2)
            cr.line_to(x2 - size * math.cos(ang - 0.4), y2 - size * math.sin(ang - 0.4))
            cr.line_to(x2 - size * math.cos(ang + 0.4), y2 - size * math.sin(ang + 0.4))
            cr.close_path()
            cr.fill()
        elif stroke.kind == "mosaic" and len(pts) >= 2:
            self._draw_mosaic(cr, pts)
        elif stroke.kind == "text" and stroke.text:
            cr.set_font_size(18)
            cr.move_to(*pts[0])
            cr.show_text(stroke.text)

    def _to_img(self, widget, ev) -> tuple[float, float]:
        vx, vy = self._event_xy(widget, ev)
        vis_w, vis_h = self._view_size()
        ix = vx * self.img_w / vis_w
        iy = vy * self.img_h / vis_h
        return ix, iy

    def _hit_bar(self, x, y) -> str | None:
        slots = self._bar_slots
        if slots:
            py, bh, items, bw = slots
            if py <= y <= py + bh:
                for kind, sx in items:
                    if sx <= x <= sx + bw:
                        return kind
        colors = self._color_slots
        if colors:
            cy, items = colors
            if cy <= y <= cy + 16:
                for rgb, cx, _cy in items:
                    if cx <= x <= cx + 18:
                        self.color = rgb
                        self.da.queue_draw()
                        return "color"
        return None

    def _dup_event(self, ev) -> bool:
        key = (int(ev.time), int(ev.type), int(getattr(ev, "button", 0)), round(ev.x, 1), round(ev.y, 1))
        if key == self._last_ev:
            return True
        self._last_ev = key
        return False

    def _on_enter(self, _w, ev):
        self.session.note_active(self)
        return False

    def _on_press(self, widget, ev):
        if self._dup_event(ev):
            return True
        self.session.note_active(self)
        if ev.button == 3:
            self.session.finish(None)
            return True
        if ev.button != 1:
            return False
        vx, vy = self._event_xy(widget, ev)
        hit = self._hit_bar(vx, vy)
        if hit == "color":
            return True
        if hit is not None:
            self._bar_action(hit)
            return True
        if not self.session.ready:
            return True
        dx, dy = self._view_to_desk(vx, vy)
        ix, iy = self._to_img(widget, ev)
        handle = self._hit_handle(ix, iy)
        box = self.session.sel_rect()
        if handle in HANDLE_NAMES:
            self.session.begin_resize(handle, dx, dy)
            return True
        if handle == "move":
            self.session.begin_move(dx, dy)
            return True
        if handle == "inside" and self.tool in TOOLS:
            if self.tool == "text":
                self._add_text(ix, iy)
                return True
            self.draft = Stroke(self.tool, self.color, self.pen_w, [(ix, iy)])
            self.da.queue_draw()
            return True
        if handle == "inside":
            self.session.begin_move(dx, dy)
            return True
        if ev.type == Gdk.EventType._2BUTTON_PRESS:
            if box is not None:
                self.session.accept()
            else:
                self.session.set_rect(QRect(self.desk))
                self.session.accept()
            return True
        self.session.begin_select(dx, dy)
        return True

    def _on_motion(self, widget, ev):
        if self._dup_event(ev):
            return True
        dx, dy = self._event_desk(widget, ev)
        ix, iy = self._to_img(widget, ev)
        if self.session.drag:
            self.session.update_drag(dx, dy)
            return True
        if self.draft is not None:
            if self.draft.kind == "pen":
                self.draft.points.append((ix, iy))
            elif len(self.draft.points) == 1:
                self.draft.points.append((ix, iy))
            else:
                self.draft.points[-1] = (ix, iy)
            self.da.queue_draw()
            return True
        handle = self._hit_handle(ix, iy)
        if handle in HANDLE_CURSORS:
            self._set_cursor(HANDLE_CURSORS[handle])
        else:
            self._set_cursor("crosshair")
            self.session.hover_at(dx, dy)
        return True

    def _on_release(self, widget, ev):
        if self._dup_event(ev):
            return True
        if ev.button != 1:
            return False
        dx, dy = self._event_desk(widget, ev)
        if self.draft is not None:
            if len(self.draft.points) >= 2:
                self.strokes.append(self.draft)
                self.redo.clear()
            self.draft = None
        self.session.end_drag(dx, dy)
        self.da.queue_draw()
        return True

    def _mosaic_box(self, pts: list[tuple[float, float]]) -> tuple[int, int, int, int] | None:
        x1, y1 = pts[0]
        x2, y2 = pts[-1]
        x = max(0, int(min(x1, x2)))
        y = max(0, int(min(y1, y2)))
        w = min(self.img_w - x, int(abs(x2 - x1)))
        h = min(self.img_h - y, int(abs(y2 - y1)))
        if w < 2 or h < 2:
            return None
        return x, y, w, h

    def _draw_mosaic(self, cr, pts: list[tuple[float, float]]) -> None:
        box = self._mosaic_box(pts)
        if box is None:
            return
        x, y, w, h = box
        block = 14
        small_w = max(1, (w + block - 1) // block)
        small_h = max(1, (h + block - 1) // block)
        try:
            src = self.pixbuf.new_subpixbuf(x, y, w, h)
            tiny = src.scale_simple(small_w, small_h, GdkPixbuf.InterpType.NEAREST)
            pixelated = tiny.scale_simple(w, h, GdkPixbuf.InterpType.NEAREST)
        except Exception:
            cr.set_source_rgb(0.35, 0.36, 0.38)
            cr.rectangle(x, y, w, h)
            cr.fill()
            return
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        Gdk.cairo_set_source_pixbuf(cr, pixelated, x, y)
        cr.paint()
        cr.restore()

    def _bar_action(self, kind: str) -> None:
        if kind in TOOLS:
            self.tool = kind
            self.session.sync_tool(kind)
        elif kind == "undo":
            self._undo()
        elif kind == "redo":
            self._redo()
        elif kind == "save":
            self.session.save()
        elif kind == "pin":
            self.session.accept(pin=True)
        elif kind == "ok":
            self.session.accept()
        elif kind == "cancel":
            self.session.finish(None)
        self.da.queue_draw()

    def _undo(self) -> None:
        if self.strokes:
            self.redo.append(self.strokes.pop())
            self.da.queue_draw()

    def _redo(self) -> None:
        if self.redo:
            self.strokes.append(self.redo.pop())
            self.da.queue_draw()

    def _add_text(self, x, y) -> None:
        dialog = Gtk.Dialog(title="文字", transient_for=self.win, flags=0)
        dialog.add_button("确定", Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        dialog.get_content_area().pack_start(entry, True, True, 8)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            text = entry.get_text().strip()
            if text:
                self.strokes.append(Stroke("text", self.color, self.pen_w, [(x, y)], text=text))
                self.redo.clear()
        dialog.destroy()
        self.da.queue_draw()

    def render_full(self) -> GdkPixbuf.Pixbuf:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.img_w, self.img_h)
        cr = cairo.Context(surface)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        for stroke in self.strokes:
            self._draw_stroke(cr, stroke)
        full = Gdk.pixbuf_get_from_surface(surface, 0, 0, self.img_w, self.img_h)
        return full if full is not None else self.pixbuf

    def render_desk_piece(self, desk: QRect) -> tuple[QRect, GdkPixbuf.Pixbuf] | None:
        inter = desk.intersected(self.desk)
        if inter.isEmpty():
            return None
        local = self._local_rect(inter)
        if local is None:
            return None
        x, y, w, h = [int(round(v)) for v in local]
        x = max(0, min(x, self.img_w - 1))
        y = max(0, min(y, self.img_h - 1))
        w = max(1, min(w, self.img_w - x))
        h = max(1, min(h, self.img_h - y))
        full = self.render_full()
        try:
            piece = full.new_subpixbuf(x, y, w, h).copy()
        except Exception:
            return None
        return inter, piece

    def destroy_quiet(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._seat is not None:
            try:
                self._seat.ungrab()
            except Exception:
                pass
            self._seat = None
        try:
            self.win.hide()
            self.win.destroy()
        except Exception:
            pass

    def _on_key(self, _w, ev):
        key = Gdk.keyval_name(ev.keyval) or ""
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        if key in {"Escape"}:
            self.session.finish(None)
            return True
        if key in {"Return", "KP_Enter"}:
            self.session.accept()
            return True
        if ctrl and key in {"s", "S"}:
            self.session.save()
            return True
        if ctrl and key in {"c", "C"}:
            self.session.accept()
            return True
        if ctrl and key in {"y", "Y"} or (ctrl and shift and key in {"z", "Z"}):
            self._redo()
            return True
        if ctrl and key in {"z", "Z"}:
            self._undo()
            return True
        if key in {"bracketleft"}:
            self.pen_w = max(2, self.pen_w - 1)
            return True
        if key in {"bracketright"}:
            self.pen_w = min(16, self.pen_w + 1)
            return True
        if key in {str(n) for n in range(1, 10)}:
            self.session.grab_index(int(key) - 1)
            return True
        return False

    def _on_delete(self, *_a):
        self.session.finish(None)
        return True


class OverlaySession(QObject):
    finished = pyqtSignal()

    def __init__(self, shots: list[ScreenShot]):
        super().__init__()
        self.pins: list = []
        self.result: QImage | None = None
        self.pin = False
        self.ready = True
        self._emitted = False
        self.active: GtkOverlay | None = None
        self._used_monitors: set[int] = set()
        self.sel: list[float] | None = None
        self.drag: str = ""
        self._anchor: list[float] | None = None
        self._rect_anchor: QRect | None = None
        self.hover: TopWindow | None = None
        self.windows: list[TopWindow] = []
        self.overlays: list[GtkOverlay] = []
        for shot in shots:
            overlay = GtkOverlay(shot, self)
            self._used_monitors.add(overlay.mon_idx)
            self.overlays.append(overlay)
        if self.overlays:
            self.active = self.overlays[0]
        _init_atspi()
        bounds = QRect(self.desktop_bounds()) if self.overlays else QRect()
        monitors = self.monitor_rects()
        threading.Thread(
            target=self._load_windows,
            args=(bounds, monitors),
            name="kuai-shot-windows",
            daemon=True,
        ).start()

    def monitor_rects(self) -> list[QRect]:
        return [QRect(overlay.desk) for overlay in self.overlays]

    def _load_windows(self, bounds: QRect, monitors: list[QRect]) -> None:
        try:
            windows = list_visible_windows(
                bounds if bounds.width() > 0 else None,
                monitors or None,
            )
        except Exception:
            windows = []
        GLib.idle_add(self._set_windows, windows)

    def _set_windows(self, windows: list[TopWindow]) -> bool:
        self.windows = windows
        return False

    def note_active(self, overlay: GtkOverlay) -> None:
        self.active = overlay

    def sync_tool(self, tool: str) -> None:
        for overlay in self.overlays:
            overlay.tool = tool

    def desktop_bounds(self) -> QRect:
        box = QRect(self.overlays[0].desk)
        for overlay in self.overlays[1:]:
            box = box.united(overlay.desk)
        return box

    def sel_rect(self) -> QRect | None:
        if not self.sel:
            return None
        x1, y1, x2, y2 = self.sel
        rect = QRect(
            int(round(min(x1, x2))),
            int(round(min(y1, y2))),
            max(1, int(round(abs(x2 - x1)))),
            max(1, int(round(abs(y2 - y1)))),
        )
        return rect.intersected(self.desktop_bounds())

    def hover_rect(self) -> QRect | None:
        return QRect(self.hover.rect) if self.hover is not None else None

    def set_rect(self, rect: QRect) -> None:
        rect = rect.intersected(self.desktop_bounds())
        self.sel = [rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()]
        self.redraw()

    def toolbar_owner(self) -> GtkOverlay | None:
        box = self.sel_rect()
        if box is None:
            return self.active
        best, area = None, -1
        for overlay in self.overlays:
            inter = overlay.desk.intersected(box)
            size = inter.width() * inter.height()
            if size > area:
                best, area = overlay, size
        return best

    def hover_at(self, dx: float, dy: float) -> None:
        if self.sel_rect() is not None or self.drag:
            return
        win = window_at(dx, dy, self.windows, self.monitor_rects())
        if win != self.hover:
            self.hover = win
            self.redraw()

    def begin_select(self, dx: float, dy: float) -> None:
        self.drag = "select"
        self._anchor = [dx, dy]
        self.sel = [dx, dy, dx, dy]
        self.hover = None
        self.redraw()

    def begin_move(self, dx: float, dy: float) -> None:
        box = self.sel_rect()
        if box is None:
            return
        self.drag = "move"
        self._anchor = [dx, dy]
        self._rect_anchor = QRect(box)
        self.redraw()

    def begin_resize(self, handle: str, dx: float, dy: float) -> None:
        box = self.sel_rect()
        if box is None:
            return
        self.drag = f"resize:{handle}"
        self._anchor = [dx, dy]
        self._rect_anchor = QRect(box)
        self.redraw()

    def update_drag(self, dx: float, dy: float) -> None:
        if self.drag == "select" and self._anchor is not None:
            self.sel = [self._anchor[0], self._anchor[1], dx, dy]
        elif self.drag == "move" and self._rect_anchor is not None and self._anchor is not None:
            rect = self._rect_anchor.translated(int(dx - self._anchor[0]), int(dy - self._anchor[1]))
            self.set_rect(rect)
            return
        elif self.drag.startswith("resize:") and self._rect_anchor is not None:
            handle = self.drag.split(":", 1)[1]
            rect = QRect(self._rect_anchor)
            if "n" in handle:
                rect.setTop(int(dy))
            if "s" in handle:
                rect.setBottom(int(dy))
            if "w" in handle:
                rect.setLeft(int(dx))
            if "e" in handle:
                rect.setRight(int(dx))
            self.set_rect(rect.normalized())
            return
        self.redraw()

    def end_drag(self, dx: float, dy: float) -> None:
        mode = self.drag
        self.drag = ""
        if mode == "select" and self._anchor is not None:
            if abs(dx - self._anchor[0]) < 8 and abs(dy - self._anchor[1]) < 8:
                win = window_at(self._anchor[0], self._anchor[1], self.windows, self.monitor_rects())
                if win is not None:
                    self.set_rect(QRect(win.rect))
                    self._anchor = None
                    return
                self.sel = None
        self._anchor = None
        self._rect_anchor = None
        box = self.sel_rect()
        if box is None or box.width() < 3 or box.height() < 3:
            self.sel = None
        self.redraw()

    def grab_index(self, index: int) -> None:
        if not self.ready or index < 0 or index >= len(self.overlays):
            return
        self.set_rect(QRect(self.overlays[index].desk))
        self.accept()

    def apply_shots(self, shots: list[ScreenShot]) -> None:
        if self._emitted:
            return
        unused = list(shots)
        for overlay in self.overlays:
            shot = next((item for item in unused if item.name and item.name == overlay.name), None)
            if shot is None and unused:
                shot = unused[0]
            if shot is not None:
                unused = [item for item in unused if item is not shot]
                overlay.apply_shot(shot)
        self.ready = True
        self.redraw()

    def export_image(self) -> QImage | None:
        box = self.sel_rect()
        if box is None:
            target = self.active or self.overlays[0]
            box = QRect(target.desk)
        pieces: list[tuple[QRect, QImage]] = []
        for overlay in self.overlays:
            item = overlay.render_desk_piece(box)
            if item is None:
                continue
            inter, pix = item
            pieces.append((inter, pixbuf_to_qimage(pix)))
        if not pieces:
            return None
        canvas = QImage(max(1, box.width()), max(1, box.height()), QImage.Format_ARGB32)
        canvas.fill(0)
        painter = QPainter(canvas)
        for inter, image in pieces:
            dest = QRect(inter.x() - box.x(), inter.y() - box.y(), inter.width(), inter.height())
            if image.width() != dest.width() or image.height() != dest.height():
                image = image.scaled(dest.width(), dest.height())
            painter.drawImage(dest.topLeft(), image)
        painter.end()
        return canvas

    def export_pixbuf(self) -> GdkPixbuf.Pixbuf | None:
        image = self.export_image()
        return qimage_to_pixbuf(image) if image is not None and not image.isNull() else None

    def accept(self, pin: bool = False) -> None:
        if self._emitted or not self.ready:
            return
        image = self.export_image()
        if image is None or image.isNull():
            return
        pix = qimage_to_pixbuf(image)
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_image(pix)
        clip.store()
        self.pin = pin
        if pin:
            self.pins.append(PinWindow(pix))
        self.finish(image)

    def save(self) -> None:
        if not self.ready:
            return
        pix = self.export_pixbuf()
        if pix is None:
            return
        owner = self.toolbar_owner() or self.overlays[0]
        dialog = Gtk.FileChooserNative.new(
            "保存截图",
            owner.win,
            Gtk.FileChooserAction.SAVE,
            "保存",
            "取消",
        )
        dialog.set_current_folder(str(pictures_dir()))
        dialog.set_current_name(datetime.now().strftime("截图-%Y%m%d-%H%M%S.png"))
        dialog.set_do_overwrite_confirmation(True)
        filt = Gtk.FileFilter()
        filt.set_name("PNG 图片")
        filt.add_pattern("*.png")
        dialog.add_filter(filt)
        if dialog.run() != Gtk.ResponseType.ACCEPT:
            dialog.destroy()
            return
        path = dialog.get_filename() or ""
        dialog.destroy()
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        pix.savev(path, "png", [], [])
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_image(pix)
        clip.store()
        self.finish(pixbuf_to_qimage(pix))

    def redraw(self) -> None:
        for overlay in self.overlays:
            overlay.da.queue_draw()

    def finish(self, image: QImage | None) -> None:
        if self._emitted:
            return
        self._emitted = True
        self.result = image
        for overlay in self.overlays:
            overlay.destroy_quiet()
        self.finished.emit()

    def close_all(self) -> None:
        self.finish(None)
