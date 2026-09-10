from __future__ import annotations

from collections.abc import Callable


def attach_timeout(ctx, interval_ms: int, callback: Callable[[], None]):
    from gi.repository import GLib

    def _once(*_a):
        try:
            callback()
        except Exception:
            pass
        return False

    src = GLib.timeout_source_new(max(1, int(interval_ms)))
    src.set_callback(_once)
    src.attach(ctx)
    return src


def attach_idle(ctx, callback: Callable[[], None]):
    from gi.repository import GLib

    def _once(*_a):
        try:
            callback()
        except Exception:
            pass
        return False

    src = GLib.idle_source_new()
    src.set_callback(_once)
    src.attach(ctx)
    try:
        ctx.wakeup()
    except Exception:
        pass
    return src


def destroy_source(src) -> None:
    if src is None:
        return
    try:
        src.destroy()
    except Exception:
        pass


def unsubscribe(conn, sub_id) -> None:
    if conn is None or sub_id is None:
        return
    try:
        conn.signal_unsubscribe(sub_id)
    except Exception:
        pass


def close_connection(conn) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass
