"""Local face DB. Names via :8080/faces. OpenAI is not the face store."""

from __future__ import annotations

import io
import math
import os
import sqlite3
import struct
import threading
import time

# /app/data is the hub's persistent volume (hub/data on the host).
DB_PATH = os.environ.get("HUB_FACE_DB") or (
    "/app/data/faces.db" if os.path.isdir("/app/data") else "/tmp/victor-faces.db"
)
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
    cols = {r[1] for r in conn.execute("PRAGMA table_info(faces)").fetchall()}
    if "jpeg" not in cols:  # reference photo for the VLM matcher (face_id.py)
        conn.execute("ALTER TABLE faces ADD COLUMN jpeg BLOB")
    # extra local (SFace) embeddings learned from NVIDIA-confirmed sightings (face_watch.py)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS face_emb ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, emb BLOB NOT NULL,"
        "source TEXT NOT NULL DEFAULT '', created REAL NOT NULL)"
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
                "INSERT INTO faces(name, embedding, created, jpeg) VALUES(?,?,?,?)",
                (name, pack(vec), time.time(), jpeg),
            )
            self.conn.commit()
            return {"id": cur.lastrowid, "name": name}

    def list(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, name, created, jpeg IS NOT NULL FROM faces ORDER BY id"
            ).fetchall()
        return [{"id": r[0], "name": r[1], "created": r[2], "photo": bool(r[3])} for r in rows]

    def image(self, fid: int) -> bytes:
        with self.lock:
            row = self.conn.execute("SELECT jpeg FROM faces WHERE id=?", (fid,)).fetchone()
        return bytes(row[0]) if row and row[0] else b""

    def delete_name(self, name: str) -> int:
        with self.lock:
            cur = self.conn.execute("DELETE FROM faces WHERE lower(name)=lower(?)", (name.strip(),))
            self.conn.execute("DELETE FROM face_emb WHERE lower(name)=lower(?)", (name.strip(),))
            self.conn.commit()
            return cur.rowcount

    def photos(self) -> list[tuple[int, str, bytes]]:
        """All stored reference photos (id, name, jpeg) for the local matcher."""
        with self.lock:
            rows = self.conn.execute("SELECT id, name, jpeg FROM faces WHERE jpeg IS NOT NULL ORDER BY id").fetchall()
        return [(r[0], r[1], bytes(r[2])) for r in rows]

    def names(self) -> list[str]:
        with self.lock:
            rows = self.conn.execute("SELECT name FROM faces GROUP BY lower(name) ORDER BY min(id)").fetchall()
        return [r[0] for r in rows]

    def add_embedding(self, name: str, emb: bytes, source: str = "", keep: int = 20) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO face_emb(name, emb, source, created) VALUES(?,?,?,?)",
                              (name, emb, source, time.time()))
            self.conn.execute(
                "DELETE FROM face_emb WHERE lower(name)=lower(?) AND id NOT IN "
                "(SELECT id FROM face_emb WHERE lower(name)=lower(?) ORDER BY id DESC LIMIT ?)", (name, name, keep))
            self.conn.commit()

    def embeddings(self) -> list[tuple[str, bytes]]:
        with self.lock:
            rows = self.conn.execute("SELECT name, emb FROM face_emb ORDER BY id").fetchall()
        return [(r[0], bytes(r[1])) for r in rows]

    def refs(self, limit: int = 6, per_name: int = 2) -> list[tuple[str, bytes]]:
        """Newest reference photos, at most per_name per person, limit in total."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT name, jpeg FROM faces WHERE jpeg IS NOT NULL ORDER BY id DESC"
            ).fetchall()
        out: list[tuple[str, bytes]] = []
        seen: dict[str, int] = {}
        for name, jpeg in rows:
            key = name.lower()
            if seen.get(key, 0) >= per_name:
                continue
            seen[key] = seen.get(key, 0) + 1
            out.append((name, bytes(jpeg)))
            if len(out) >= limit:
                break
        return out

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
