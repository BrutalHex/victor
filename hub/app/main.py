#!/usr/bin/env python3
"""Hub brain process. Phase 0: receive VCT1 SENSOR, HTTP status, TCP heartbeat.

OpenAI keys stay in this process / container env. Never write them to the robot.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from protocol import TYPE_SENSOR, decode, unpack_sensor

STATE = {
    "sensors": 0,
    "last_seq": None,
    "last_ns": None,
    "last_sensor": None,
    "started": time.time(),
}
LOCK = threading.Lock()


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
        with LOCK:
            STATE["sensors"] += 1
            STATE["last_seq"] = hdr.seq
            STATE["last_ns"] = hdr.t_ns
            STATE["last_sensor"] = sensor
            STATE["last_from"] = addr[0]


def tcp_heartbeat(host: str, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(16)
    print(f"hub heartbeat TCP {host}:{port}", flush=True)
    while True:
        conn, _ = sock.accept()
        conn.close()


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
    threading.Thread(target=udp_loop, args=(host, 7502), daemon=True).start()
    threading.Thread(target=tcp_heartbeat, args=(host, 7443), daemon=True).start()
    httpd = ThreadingHTTPServer((host, 8080), Status)
    print("hub HTTP 8080", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
