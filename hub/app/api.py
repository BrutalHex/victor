"""Keep-alive HTTPS to api.openai.com.

urllib opened a new TCP + TLS connection for every STT, chat and TTS call
(~0.3-1 s each from the hub). Api keeps a small pool of open connections,
retries once when a pooled one turns out to be closed by the server, and can
warm a connection while the user is still talking.
"""

from __future__ import annotations

import http.client
import threading
import time

HOST = "api.openai.com"
IDLE_MAX_S = 110.0  # don't reuse connections idle longer than this


class ApiError(OSError):
    def __init__(self, status: int, body: bytes) -> None:
        super().__init__(f"HTTP Error {status}")
        self.code = status
        self.body = body

    def read(self) -> bytes:  # mirrors urllib.error.HTTPError for _http_error
        return self.body


_STALE = (
    http.client.RemoteDisconnected,
    http.client.CannotSendRequest,
    http.client.BadStatusLine,
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
)


class Api:
    CALLS: dict[str, int] = {}  # per endpoint, all instances (asleep must stay 0 for audio)

    def __init__(self, key: str, host: str = HOST) -> None:
        self.key = key
        self.host = host
        self._lock = threading.Lock()
        self._idle: list[tuple[http.client.HTTPSConnection, float]] = []
        self.connects = 0  # new TLS connections opened (stats/tests)

    def _new(self, timeout: float) -> http.client.HTTPSConnection:
        conn = http.client.HTTPSConnection(self.host, timeout=timeout)
        conn.connect()
        self.connects += 1
        return conn

    def _take(self) -> http.client.HTTPSConnection | None:
        now = time.monotonic()
        with self._lock:
            while self._idle:
                conn, since = self._idle.pop()
                if now - since <= IDLE_MAX_S:
                    return conn
                conn.close()
        return None

    def _give(self, conn: http.client.HTTPSConnection) -> None:
        with self._lock:
            self._idle.append((conn, time.monotonic()))
            while len(self._idle) > 3:
                self._idle.pop(0)[0].close()

    def warm(self) -> None:
        """Open a connection in the background so the next call skips TLS."""
        with self._lock:
            if self._idle:
                return

        def run() -> None:
            try:
                self._give(self._new(10))
            except OSError:
                pass

        threading.Thread(target=run, daemon=True).start()

    def post(self, path: str, body: bytes, content_type: str, timeout: float) -> bytes:
        Api.CALLS[path] = Api.CALLS.get(path, 0) + 1
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": content_type,
            "Connection": "keep-alive",
        }
        for attempt in (0, 1):
            conn = self._take()
            reused = conn is not None
            if conn is None:
                conn = self._new(timeout)
            if conn.sock is not None:
                conn.sock.settimeout(timeout)
            try:
                conn.request("POST", path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            except _STALE:
                conn.close()
                if reused and attempt == 0:
                    continue  # the server dropped an idle connection
                raise
            except BaseException:
                conn.close()
                raise
            if resp.will_close:
                conn.close()
            else:
                self._give(conn)
            if resp.status >= 400:
                raise ApiError(resp.status, data)
            return data
        raise ConnectionError("unreachable")
