from __future__ import annotations

import json
import os
import socket
import threading
from collections.abc import Callable

from .paths import socket_path


def send_command(cmd: str, timeout: float = 1.2) -> bool:
    path = str(socket_path())
    if not os.path.exists(path):
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(path)
        sock.sendall((json.dumps({"cmd": cmd}) + "\n").encode("utf-8"))
        sock.close()
        return True
    except OSError:
        return False


class CommandServer:
    def __init__(self, handler: Callable[[str], None]):
        self.handler = handler
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        path = str(socket_path())
        try:
            os.unlink(path)
        except OSError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(path)
        os.chmod(path, 0o600)
        sock.listen(4)
        sock.settimeout(0.5)
        self._sock = sock
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        try:
            os.unlink(str(socket_path()))
        except OSError:
            pass

    def _loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                data = conn.recv(4096).decode("utf-8", errors="ignore")
                conn.close()
                line = data.splitlines()[0] if data else ""
                payload = json.loads(line) if line else {}
                cmd = str(payload.get("cmd") or "")
                if cmd:
                    self.handler(cmd)
            except Exception:
                continue
