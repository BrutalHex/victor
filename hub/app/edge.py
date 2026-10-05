"""Tiny ONNX edge model. Votes stop/back_off only. IR cliffs still win."""

from __future__ import annotations

import glob
import os

from explore import BACK_OFF, STOP

# logits: 0=safe 1=edge 2=obstacle 3=unknown
LABELS = {1: STOP, 2: BACK_OFF}


class Edge:
    def __init__(self, model_dir: str | None = None) -> None:
        self.session = None
        self.path = ""
        root = model_dir or os.environ.get("HUB_MODEL_DIR", os.path.join(os.path.dirname(__file__), "..", "models"))
        paths = []
        if os.path.isdir(root):
            paths = sorted(glob.glob(os.path.join(root, "*.onnx")))
        env = os.environ.get("EDGE_MODEL")
        if env:
            paths = [env] + paths
        for p in paths:
            try:
                import onnxruntime as ort  # type: ignore

                self.session = ort.InferenceSession(p, providers=["CPUExecutionProvider"])
                self.path = p
                break
            except Exception:
                continue

    def vote(self, jpeg: bytes) -> int:
        if not jpeg or self.session is None:
            return 0
        try:
            import numpy as np  # type: ignore
        except Exception:
            return 0
        arr = _jpeg_chw(jpeg)
        if arr is None:
            return 0
        inp = self.session.get_inputs()[0]
        out = self.session.run(None, {inp.name: arr})[0]
        logits = out.reshape(-1)
        idx = int(logits.argmax())
        return LABELS.get(idx, 0)


def _jpeg_chw(jpeg: bytes):
    try:
        import io
        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore

        im = Image.open(io.BytesIO(jpeg)).convert("RGB").resize((160, 90))
        arr = np.asarray(im, dtype="float32") / 255.0
        return arr.transpose(2, 0, 1)[None, ...]
    except Exception:
        return None
