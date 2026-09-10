from __future__ import annotations

import traceback
from datetime import datetime
from threading import Lock

from .paths import log_path

_LOCK = Lock()


def _fmt(value: object) -> str:
    if value is None:
        return "-"
    text = str(value).replace("\n", " ").replace("\r", " ")
    if " " in text:
        return f"\"{text}\""
    return text


def event(name: str, **fields: object) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    extra = " ".join(f"{key}={_fmt(val)}" for key, val in fields.items())
    line = f"{stamp} {name}" + (f" {extra}" if extra else "")
    path = log_path()
    with _LOCK:
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
        print(line, flush=True)


def exception(name: str, exc: BaseException, **fields: object) -> None:
    event(name, err=exc, **fields)
    detail = traceback.format_exc().strip()
    if detail:
        event(f"{name}.trace", text=detail.replace("\n", " | "))
