"""Classical hub veto. If this disagrees with the ONNX model, classical wins."""

from __future__ import annotations

from explore import STOP

def classical_vote(sensor: dict | None) -> int:
    if not sensor:
        return 0
    cliffs = sensor.get("cliffs") or (0, 0, 0, 0)
    if cliffs[0] < 80 or cliffs[1] < 80:
        return STOP
    return 0
