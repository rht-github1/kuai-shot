from __future__ import annotations

import os
import traceback
from datetime import datetime
from pathlib import Path
from threading import Lock

from .paths import log_flag_path, log_path

_LOCK = Lock()
_MAX_LOG = 2 * 1024 * 1024
_enabled = False
_loaded = False


def _env_flag() -> bool | None:
    raw = (os.environ.get("KUAI_SHOT_LOG") or "").strip().lower()
    if raw in {"1", "true", "on", "yes"}:
        return True
    if raw in {"0", "false", "off", "no"}:
        return False
    return None


def _read_flag() -> bool:
    path = log_flag_path()
    try:
        text = path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return False
    return text in {"1", "on", "true", "yes"}


def _write_flag(on: bool) -> None:
    path = log_flag_path()
    try:
        path.write_text("on\n" if on else "off\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass


def load_enabled() -> bool:
    global _enabled, _loaded
    with _LOCK:
        env = _env_flag()
        if env is not None:
            _enabled = env
        else:
            _enabled = _read_flag()
        _loaded = True
        return _enabled


def is_enabled() -> bool:
    if not _loaded:
        load_enabled()
    return _enabled


def set_enabled(on: bool, persist: bool = True) -> bool:
    global _enabled, _loaded
    on = bool(on)
    with _LOCK:
        _enabled = on
        _loaded = True
        if persist:
            _write_flag(on)
    return _enabled


def apply_cli_log(args: list[str]) -> int:
    from .ipc import send_command

    sub = (args[0] if args else "status").lower()
    if sub in {"on", "1", "true", "yes"}:
        set_enabled(True)
        send_command("log-on")
        print(f"调试日志已打开：{log_path()}")
        return 0
    if sub in {"off", "0", "false", "no"}:
        set_enabled(False)
        send_command("log-off")
        print("调试日志已关闭")
        return 0
    print("开" if is_enabled() else "关")
    if is_enabled():
        print(str(log_path()))
    return 0


def rotate_if_needed(path: Path, max_bytes: int = _MAX_LOG) -> None:
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return
        bak = path.with_name(path.name + ".1")
        try:
            bak.unlink()
        except OSError:
            pass
        path.replace(bak)
    except OSError:
        pass


def _fmt(value: object) -> str:
    if value is None:
        return "-"
    text = str(value).replace("\n", " ").replace("\r", " ")
    if " " in text:
        return f"\"{text}\""
    return text


def event(name: str, **fields: object) -> None:
    if not is_enabled():
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    extra = " ".join(f"{key}={_fmt(val)}" for key, val in fields.items())
    line = f"{stamp} {name}" + (f" {extra}" if extra else "")
    path = log_path()
    with _LOCK:
        try:
            rotate_if_needed(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
        print(line, flush=True)


def exception(name: str, exc: BaseException, **fields: object) -> None:
    if not is_enabled():
        return
    event(name, err=exc, **fields)
    detail = traceback.format_exc().strip()
    if detail:
        event(f"{name}.trace", text=detail.replace("\n", " | "))
