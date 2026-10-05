#!/usr/bin/env python3
"""Build a tiny ONNX classifier: 1x3x90x160 → 4 logits, bias toward unknown.

Drop a real NVIDIA TAO DetectNet_v2 / MobileNet-V2 export in this directory
to replace this stand-in. The stand-in is CPU-first and votes unknown so IR
cliffs on the robot remain the hard stop.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        import numpy as np
        from onnx import TensorProto, helper, numpy_helper, save
    except ImportError:
        print("onnx not installed; skip tiny_edge.onnx", file=sys.stderr)
        return 0

    out = os.path.join(os.path.dirname(__file__), "tiny_edge.onnx")
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, 90, 160])
    y = helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, 4])
    reduce = helper.make_node("ReduceMean", ["input"], ["pooled"], axes=[2, 3], keepdims=0)
    w = np.zeros((3, 4), dtype=np.float32)
    b = np.array([0, 0, 0, 3], dtype=np.float32)
    gemm = helper.make_node("Gemm", ["pooled", "W", "B"], ["logits"])
    graph = helper.make_graph(
        [reduce, gemm],
        "tiny_edge",
        [x],
        [y],
        [numpy_helper.from_array(w, name="W"), numpy_helper.from_array(b, name="B")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    save(model, out)
    print("wrote", out, "bytes", os.path.getsize(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
