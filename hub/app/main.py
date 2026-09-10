#!/usr/bin/env python3
"""Hub brain. Phase 2: VCT1 SENSOR, heartbeat+skills on :7443, explorer."""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from explore import NAMES, Explorer
from protocol import TYPE_SENSOR, decode, unpack_sensor

STATE = {
    "sensors": 0,
    "last_seq": None,
    "last_ns": None,
    "last_sensor": None,
    "started": time.time(),
    "hz": 0.0,
    "skill": "idle",
    "veto": 0,
    "heartbeats": 0,
}
_WINDOW = []
LOCK = threading.Lock()
EXPLORER = Explorer()


def udp_loop(host: str, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    print(f"hub SENSOR UDP {host}:{port}", flush=True)
    while True:
        buf, addr = sock.recvfrom(65535)
        try:
            hdr, payload = decode(buf)
        except ValueError:
            continue
        if hdr.type != TYPE_SENSOR:
            continue
        try:
            sensor = unpack_sensor(payload)
        except ValueError:
            continue
        now = time.time()
        with LOCK:
            STATE["sensors"] += 1
            STATE["last_seq"] = hdr.seq
            STATE["last_ns"] = hdr.t_ns
            STATE["last_sensor"] = sensor
            STATE["last_from"] = addr[0]
            _WINDOW.append(now)
            cutoff = now - 1.0
            while _WINDOW and _WINDOW[0] < cutoff:
                _WINDOW.pop(0)
            STATE["hz"] = float(len(_WINDOW))


def handle_robot(conn: socket.socket) -> None:
    conn.settimeout(2.0)
    while True:
        buf = b""
        while len(buf) < 18:
            chunk = conn.recv(18 - len(buf))
            if not chunk:
                return
            buf += chunk
        if buf[:4] != b"VHB1":
            return
        veto = buf[17]
        with LOCK:
            sensor = STATE.get("last_sensor")
            STATE["veto"] = veto
            STATE["heartbeats"] += 1
            skill = EXPLORER.step(sensor, veto)
            STATE["skill"] = NAMES.get(skill, "idle")
        t_ns = time.time_ns()
        reply = b"VHB1" + bytes([2]) + buf[5:9] + struct.pack("<Q", t_ns) + bytes([skill])
        conn.sendall(reply)


def tcp_loop(host: str, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(16)
    print(f"hub heartbeat+skill TCP {host}:{port}", flush=True)
    while True:
        conn, _ = sock.accept()
        threading.Thread(target=lambda c=conn: (handle_robot(c), c.close()), daemon=True).start()


class Status(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in ("/", "/status", "/healthz"):
            self.send_error(404)
            return
        with LOCK:
            body = json.dumps(STATE, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        print("openai key present on hub only", flush=True)
    host = "0.0.0.0"
    udp_port = int(os.environ.get("HUB_SENSOR_PORT", "7502"))
    tcp_port = int(os.environ.get("HUB_SKILL_PORT", "7443"))
    http_port = int(os.environ.get("HUB_HTTP_PORT", "8080"))
    threading.Thread(target=udp_loop, args=(host, udp_port), daemon=True).start()
    threading.Thread(target=tcp_loop, args=(host, tcp_port), daemon=True).start()
    httpd = ThreadingHTTPServer((host, http_port), Status)
    print(f"hub HTTP {http_port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
