"""VCT1 UDP framing — keep in sync with robot/agent/internal/vct1."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

MAGIC = b"VCT1"
TYPE_SENSOR = 1
HEADER = struct.Struct("<4sBBIQI")  # magic, type, flags, seq, t_ns, len


@dataclass
class Header:
    type: int
    flags: int
    seq: int
    t_ns: int


def encode(header: Header, payload: bytes) -> bytes:
    head = HEADER.pack(MAGIC, header.type, header.flags, header.seq, header.t_ns, len(payload))
    crc = zlib.crc32(head + payload) & 0xFFFFFFFF
    return head + payload + struct.pack("<I", crc)


def decode(buf: bytes) -> tuple[Header, bytes]:
    if len(buf) < HEADER.size + 4:
        raise ValueError("short datagram")
    magic, typ, flags, seq, t_ns, plen = HEADER.unpack(buf[: HEADER.size])
    if magic != MAGIC:
        raise ValueError("bad magic")
    need = HEADER.size + plen + 4
    if len(buf) < need:
        raise ValueError("truncated")
    payload = buf[HEADER.size : HEADER.size + plen]
    got = struct.unpack("<I", buf[HEADER.size + plen : need])[0]
    want = zlib.crc32(buf[: HEADER.size + plen]) & 0xFFFFFFFF
    if got != want:
        raise ValueError("crc mismatch")
    return Header(typ, flags, seq, t_ns), payload


def unpack_sensor(payload: bytes) -> dict:
    if len(payload) < 47:
        raise ValueError("short sensor")
    cliffs = struct.unpack_from("<4H", payload, 0)
    prox_mm, prox_q = struct.unpack_from("<HB", payload, 8)
    imu = struct.unpack_from("<6h", payload, 11)
    enc = struct.unpack_from("<4i", payload, 23)
    batt, charger, touch, flags = struct.unpack_from("<4H", payload, 39)
    return {
        "cliffs": cliffs,
        "prox_mm": prox_mm,
        "prox_quality": prox_q,
        "imu": imu,
        "encoders": {"rw": enc[0], "lw": enc[1], "lift": enc[2], "head": enc[3]},
        "batt_mv": batt,
        "charger_mv": charger,
        "touch": touch,
        "flags": flags,
        "on_charger": bool(flags & 4),
        "button": bool(flags & 1),
    }
