"""Local face DB. Names via :8080/faces. OpenAI is not the face store."""

from __future__ import annotations

import io
import math
import os
import sqlite3
import struct
import threading
import time

DB_PATH = os.environ.get("HUB_FACE_DB", "/tmp/victor-faces.db")
MATCH_MIN = 0.92


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS faces ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "name TEXT NOT NULL,"
        "embedding BLOB NOT NULL,"
        "created REAL NOT NULL)"
    )
    conn.commit()
    return conn


def embed(jpeg: bytes) -> list[float]:
    """64-d L2 embedding. Uses a spatial grid when Pillow is present."""
    vec = [0.0] * 64
    try:
        from PIL import Image  # type: ignore

        im = Image.open(io.BytesIO(jpeg)).convert("L").resize((32, 32))
        pix = list(im.getdata())
        for y in range(8):
            for x in range(8):
                acc = 0.0
                for dy in range(4):
                    for dx in range(4):
                        acc += pix[(y * 4 + dy) * 32 + (x * 4 + dx)]
                vec[y * 8 + x] = acc / 16.0
    except Exception:
        if not jpeg:
            return vec
        for i, b in enumerate(jpeg):
            vec[i % 64] += float(b)
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob[: n * 4]))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))


class FaceDB:
    def __init__(self, path: str = DB_PATH) -> None:
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = _connect(path)
        self.lock = threading.Lock()

    def enroll(self, name: str, jpeg: bytes) -> dict:
        name = (name or "").strip()
        if not name or not jpeg:
            raise ValueError("name and image required")
        vec = embed(jpeg)
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO faces(name, embedding, created) VALUES(?,?,?)",
                (name, pack(vec), time.time()),
            )
            self.conn.commit()
            return {"id": cur.lastrowid, "name": name}

    def list(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT id, name, created FROM faces ORDER BY id").fetchall()
        return [{"id": r[0], "name": r[1], "created": r[2]} for r in rows]

    def delete(self, fid: int) -> bool:
        with self.lock:
            cur = self.conn.execute("DELETE FROM faces WHERE id=?", (fid,))
            self.conn.commit()
            return cur.rowcount > 0

    def match(self, jpeg: bytes) -> tuple[str | None, float]:
        if not jpeg:
            return None, 0.0
        vec = embed(jpeg)
        best, score = None, 0.0
        with self.lock:
            rows = self.conn.execute("SELECT name, embedding FROM faces").fetchall()
        for name, blob in rows:
            s = cosine(vec, unpack(blob))
            if s > score:
                best, score = name, s
        if score < MATCH_MIN:
            return None, score
        return best, score
