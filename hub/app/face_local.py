"""Local face detection + embedding on the hub (CPU, OpenCV ONNX models).

- YuNet (face_detection_yunet_2023mar.onnx, ~230 KB, MIT): finds faces and 5
  landmarks in a camera frame.
- SFace (face_recognition_sface_2021dec.onnx, ~37 MB, Apache-2.0): 128-d
  embedding of an aligned face; cosine similarity against the stored
  references in the faces DB (the same photos the Face page shows).

Nothing here leaves the hub. NVIDIA is only asked (by face_watch / main, under
the shared budget) when this local match is uncertain.

Measured 10 Oct 2026 on the robot's 640x480 face camera (dim room, camera
looking up): same person across two enrollment sessions 0.34-0.72 (within one
burst ~0.91), 63 strangers vs M.O's references max 0.31. Hence:
  >= HUB_FACE_LOCAL_SURE (0.50)   -> that name, no NVIDIA call
  <  HUB_FACE_LOCAL_UNSURE (0.32) -> not anyone enrolled, no call
  in between                      -> uncertain: NVIDIA may confirm (budgeted)
"""

from __future__ import annotations

import hashlib
import os
import threading
import urllib.request

MODELS = {
    "yunet": ("face_detection_yunet_2023mar.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
              "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "sface": ("face_recognition_sface_2021dec.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
              "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def model_dirs() -> list[str]:
    return [d for d in (os.environ.get("HUB_FACE_MODEL_DIR"), "/opt/face-models", "/app/data/models",
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models")) if d]


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_model(key: str, download: bool = False) -> str:
    """Path of a verified model file, or "" (optionally download it to the first writable dir)."""
    fname, url, sha = MODELS[key]
    for d in model_dirs():
        p = os.path.join(d, fname)
        if os.path.isfile(p):
            try:
                if _sha(p) == sha:
                    return p
            except OSError:
                pass
    if not download:
        return ""
    for d in model_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, fname)
            tmp = p + ".part"
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                f.write(r.read())
            if _sha(tmp) != sha:
                os.remove(tmp)
                print(f"face model {fname}: checksum mismatch, not used", flush=True)
                return ""
            os.replace(tmp, p)
            print(f"face model {fname} downloaded to {d}", flush=True)
            return p
        except OSError:
            continue
    return ""


class Face:
    __slots__ = ("box", "score", "frontal", "raw", "emb")

    def __init__(self, raw, score: float, box: tuple[int, int, int, int], frontal: bool) -> None:
        self.raw, self.score, self.box, self.frontal, self.emb = raw, score, box, frontal, None

    @property
    def width(self) -> int:
        return self.box[2]


def frontal_ok(raw) -> bool:
    """YuNet landmarks: right eye, left eye, nose, mouth corners. Profiles have
    the eyes close together and the nose outside them."""
    x, y, w, h = (float(v) for v in raw[:4])
    rex, lex, nx = float(raw[4]), float(raw[6]), float(raw[8])
    eye = abs(lex - rex)
    if w <= 0 or eye / w < 0.30:
        return False
    frac = (nx - min(rex, lex)) / max(1.0, eye)
    return 0.05 <= frac <= 0.95


class LocalFaces:
    """Detector + embedder + gallery. available is False when OpenCV or the
    models are missing; callers then use the old NVIDIA-only path."""

    def __init__(self, download: bool | None = None) -> None:
        self.min_score = _f("HUB_FACE_DET_MIN", 0.80)
        self.min_px = int(_f("HUB_FACE_MIN_PX", 48))  # face width in a 640x480 frame (~2 m)
        self.sure = _f("HUB_FACE_LOCAL_SURE", 0.50)
        self.unsure = _f("HUB_FACE_LOCAL_UNSURE", 0.32)
        self.margin = _f("HUB_FACE_LOCAL_MARGIN", 0.08)  # best name must beat the next name by this
        self.lock = threading.Lock()
        self.error = ""
        self.available = False
        self.det = self.rec = None
        self.cv2 = self.np = None
        self.gallery: dict[int, tuple[str, object]] = {}  # faces.id -> (name, emb) from stored photos
        self.extra: list[tuple[str, object]] = []  # learned (NVIDIA-confirmed) embeddings
        self.scans = 0
        if download is None:
            download = os.environ.get("HUB_FACE_MODEL_DOWNLOAD", "1").strip().lower() not in ("0", "false", "off", "no")
        if os.environ.get("HUB_FACE_LOCAL", "1").strip().lower() in ("0", "false", "off", "no"):
            self.error = "off (HUB_FACE_LOCAL=0)"
            return
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore

            yp, sp = find_model("yunet", download), find_model("sface", download)
            if not yp or not sp:
                self.error = "face models missing"
                return
            self.det = cv2.FaceDetectorYN.create(yp, "", (320, 320), 0.6, 0.3, 50)
            self.rec = cv2.FaceRecognizerSF.create(sp, "")
            self.cv2, self.np = cv2, np
            self.available = True
        except Exception as exc:  # noqa: BLE001 - never break the hub over the local model
            self.error = f"{type(exc).__name__}: {exc}"[:160]

    # ----- per frame ----------------------------------------------------
    def decode(self, jpeg: bytes):
        if not self.available or not jpeg:
            return None
        try:
            return self.cv2.imdecode(self.np.frombuffer(jpeg, self.np.uint8), self.cv2.IMREAD_COLOR)
        except Exception:  # noqa: BLE001
            return None

    def detect(self, jpeg: bytes) -> tuple[list[Face], list[Face], object]:
        """-> (all faces, clear faces, decoded image). Clear = confident,
        big enough (min_px wide) and roughly frontal."""
        img = self.decode(jpeg)
        if img is None:
            return [], [], None
        h, w = img.shape[:2]
        with self.lock:
            self.det.setInputSize((w, h))
            _, found = self.det.detect(img)
            self.scans += 1
        faces: list[Face] = []
        for raw in ([] if found is None else found):
            box = tuple(int(v) for v in raw[:4])
            faces.append(Face(raw, float(raw[-1]), box, frontal_ok(raw)))
        faces.sort(key=lambda f: -f.width)
        clear = [f for f in faces if f.score >= self.min_score and f.width >= self.min_px and f.frontal]
        return faces, clear, img

    def embed(self, img, face: Face):
        if face.emb is None:
            with self.lock:
                feat = self.rec.feature(self.rec.alignCrop(img, face.raw))
            v = self.np.asarray(feat, dtype=self.np.float32).reshape(-1)
            n = float(self.np.linalg.norm(v)) or 1.0
            face.emb = v / n
        return face.emb

    def one_clear_face(self, jpeg: bytes):
        """Exactly one clear face (small/background faces that are not clear are
        ignored) -> (embedding, Face); else (None, reason)."""
        faces, clear, img = self.detect(jpeg)
        if img is None:
            return None, "no image"
        if not clear:
            return None, "no clear face" if not faces else "face not clear (too small, side-on or blurry)"
        if len(clear) > 1:
            return None, "more than one face"
        return self.embed(img, clear[0]), clear[0]

    # ----- gallery ------------------------------------------------------
    def sync(self, db) -> int:
        """Embed stored reference photos not seen yet (and drop deleted ones)."""
        if not self.available or db is None:
            return 0
        rows = db.photos()
        ids = {fid for fid, _n, _j in rows}
        added = 0
        for fid, name, jpeg in rows:
            if fid in self.gallery and self.gallery[fid][0] == name:
                continue
            emb, face = self.one_clear_face(jpeg)
            if emb is None:  # older web enrolls: take the biggest clear-ish face
                faces, _clear, img = self.detect(jpeg)
                faces = [f for f in faces if f.frontal and f.width >= self.min_px] if img is not None else []
                if not faces:
                    self.gallery[fid] = (name, None)
                    continue
                emb = self.embed(img, faces[0])
            self.gallery[fid] = (name, emb)
            added += 1
        for fid in list(self.gallery):
            if fid not in ids:
                del self.gallery[fid]
        names = {n.lower() for n, _ in self.gallery.values()}
        self.extra = [(n, e) for n, e in self.extra if n.lower() in names]
        return added

    def load_extra(self, rows: list[tuple[str, bytes]]) -> None:
        self.extra = [(n, self.np.frombuffer(b, dtype=self.np.float32).copy()) for n, b in rows]

    def refs(self) -> list[tuple[str, object]]:
        return [(n, e) for n, e in self.gallery.values() if e is not None] + list(self.extra)

    def match(self, emb) -> dict:
        """-> {"name", "score", "second", "level": sure|unsure|unknown|empty}."""
        best: dict[str, float] = {}
        canon: dict[str, str] = {}
        for name, ref in self.refs():
            s = float(self.np.dot(emb, ref))
            k = name.lower()
            canon.setdefault(k, name)
            if s > best.get(k, -1.0):
                best[k] = s
        if not best:
            return {"name": None, "score": 0.0, "second": 0.0, "level": "empty"}
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        k, score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if score >= self.sure and score - second >= self.margin:
            level = "sure"
        elif score >= self.unsure:
            level = "unsure"
        else:
            level = "unknown"
        return {"name": canon[k], "score": round(score, 3), "second": round(second, 3), "level": level}

    def status(self) -> dict:
        return {"available": self.available, "error": self.error, "refs": len(self.refs()),
                "photos": len(self.gallery), "learned": len(self.extra), "scans": self.scans,
                "sure": self.sure, "unsure": self.unsure}


def cosine(a, b) -> float:
    import numpy as np  # type: ignore

    return float(np.dot(a, b))
