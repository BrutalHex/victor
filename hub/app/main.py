#!/usr/bin/env python3
"""Hub brain. SENSOR/AUDIO/VIDEO + heartbeat/skills + faces + voice + edge model."""

from __future__ import annotations

import base64
import json
import os
import queue
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from edge import Edge
from explore import NAMES, Explorer
from faces import FaceDB
from protocol import FLAG_FACE, TYPE_AUDIO, TYPE_SENSOR, TYPE_VIDEO, decode, unpack_sensor
from safety import classical_vote
from voice import MIN_UTTERANCE_BYTES, Voice, _wav_wrap, tone

STATE = {
    "sensors": 0,
    "audio": 0,
    "video": 0,
    "last_seq": None,
    "last_ns": None,
    "last_sensor": None,
    "started": time.time(),
    "hz": 0.0,
    "skill": "idle",
    "veto": 0,
    "heartbeats": 0,
    "edge_vote": "unknown",
    "face": None,
    "thinking": False,
    "last_transcript": "",
    "last_reply": "",
    "audio_rms": 0,
    "noise_rms": 0,
}
_WINDOW = []
LOCK = threading.Lock()
EXPLORER = Explorer()
FACE_DB = FaceDB(os.environ.get("HUB_FACE_DB", "/tmp/victor-faces.db"))
VOICE = Voice()
EDGE = Edge()
PENDING: list[tuple[int, bytes]] = []
UTTERANCES: queue.Queue[bytes] = queue.Queue(maxsize=2)
LAST_JPEG = {"nav": b"", "face": b""}
LAST_FACE_SPOKEN = {"name": "", "t": 0.0}
CMD_FACEUI, CMD_SPEAK, CMD_DISPLAY = 1, 2, 3


def queue_cmd(kind: int, payload: bytes) -> None:
    with LOCK:
        PENDING.append((kind, payload))


def pop_cmds() -> list[tuple[int, bytes]]:
    with LOCK:
        out = list(PENDING)
        PENDING.clear()
        return out


def udp_loop(host: str, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    print(f"hub UDP {host}:{port}", flush=True)
    while True:
        buf, addr = sock.recvfrom(65535)
        _ingest_vct1(buf, addr[0])


def _ingest_vct1(buf: bytes, src: str = "") -> None:
    try:
        hdr, payload = decode(buf)
    except ValueError:
        return
    now = time.time()
    if hdr.type == TYPE_SENSOR:
        try:
            sensor = unpack_sensor(payload)
        except ValueError:
            return
        with LOCK:
            STATE["sensors"] += 1
            STATE["last_seq"] = hdr.seq
            STATE["last_ns"] = hdr.t_ns
            STATE["last_sensor"] = sensor
            if src:
                STATE["last_from"] = src
            _WINDOW.append(now)
            cutoff = now - 1.0
            while _WINDOW and _WINDOW[0] < cutoff:
                _WINDOW.pop(0)
            STATE["hz"] = float(len(_WINDOW))
        return
    if hdr.type == TYPE_AUDIO:
        with LOCK:
            STATE["audio"] += 1
        _on_audio(payload)
        return
    if hdr.type == TYPE_VIDEO:
        with LOCK:
            STATE["video"] += 1
        if hdr.flags & FLAG_FACE:
            LAST_JPEG["face"] = payload
            _on_face_frame(payload)
        else:
            LAST_JPEG["nav"] = payload
            _on_nav_frame(payload)


def _enqueue_utterance(utt: bytes) -> None:
    dropped = False
    while True:
        try:
            UTTERANCES.put_nowait(utt)
            return
        except queue.Full:
            try:
                UTTERANCES.get_nowait()
                dropped = True
            except queue.Empty:
                print("voice queue full; dropped newest", flush=True)
                return
    if dropped:
        print("voice queue dropped oldest", flush=True)


def _on_audio(pcm: bytes) -> None:
    utt = VOICE.push(pcm)
    with LOCK:
        STATE["audio_rms"] = getattr(VOICE, "last_rms", 0)
        STATE["noise_rms"] = int(getattr(VOICE, "noise", 0))
    if not utt:
        return
    if len(utt) < MIN_UTTERANCE_BYTES:
        print(f"voice skip short {len(utt)} bytes", flush=True)
        return
    with LOCK:
        STATE["thinking"] = True
    queue_cmd(CMD_FACEUI, b"thinking|")
    print(f"voice utterance {len(utt)} bytes rms={VOICE.last_rms} noise={int(VOICE.noise)}", flush=True)
    try:
        os.makedirs("/app/data", exist_ok=True)
        open("/app/data/last.wav", "wb").write(_wav_wrap(utt))
    except OSError as exc:
        print(f"voice wav save failed {exc}", flush=True)
    _enqueue_utterance(utt)


def voice_loop() -> None:
    """OpenAI stays off the :7443 reader. Robot read deadline is 2s."""
    while True:
        pcm = UTTERANCES.get()
        text = VOICE.transcribe(pcm)
        reply = VOICE.chat(text) if text else ""
        audio = VOICE.tts(reply) if reply else b""
        VOICE.last_text = text
        VOICE.last_reply = reply
        VOICE.thinking = False
        with LOCK:
            STATE["thinking"] = False
            STATE["last_transcript"] = text
            STATE["last_reply"] = reply
        queue_cmd(CMD_FACEUI, b"idle|")
        if audio:
            queue_cmd(CMD_SPEAK, audio)
        print(f"voice transcript={text!r} reply={reply!r} speak={len(audio)}", flush=True)


def _on_nav_frame(jpeg: bytes) -> None:
    vote = EDGE.vote(jpeg)
    klass = classical_vote(None)
    with LOCK:
        sensor = STATE.get("last_sensor")
        klass = classical_vote(sensor)
        # classical wins over the model
        STATE["edge_vote"] = NAMES.get(klass or vote, "unknown")
        STATE["_edge_int"] = klass or vote


def _on_face_frame(jpeg: bytes) -> None:
    name, score = FACE_DB.match(jpeg)
    if not name:
        return
    with LOCK:
        STATE["face"] = {"name": name, "score": round(score, 3)}
    now = time.time()
    if LAST_FACE_SPOKEN["name"] == name and now - LAST_FACE_SPOKEN["t"] < 30:
        return
    LAST_FACE_SPOKEN["name"] = name
    LAST_FACE_SPOKEN["t"] = now
    queue_cmd(CMD_FACEUI, f"name|{name}".encode())
    spoken = VOICE.tts(name) if VOICE.key else tone(660, 250)
    if spoken:
        queue_cmd(CMD_SPEAK, spoken)


def handle_robot(conn: socket.socket) -> None:
    conn.settimeout(2.0)
    try:
        while True:
            buf = b""
            while len(buf) < 18:
                chunk = conn.recv(18 - len(buf))
                if not chunk:
                    return
                buf += chunk
            if buf[:4] != b"VHB1":
                return
            typ = buf[4]
            if typ == 5:
                ln = _read_n(conn, 4)
                if ln is None:
                    return
                n = struct.unpack("<I", ln)[0]
                if n > 2 << 20:
                    return
                payload = _read_n(conn, n) if n else b""
                if payload is None:
                    return
                _ingest_vct1(payload)
                continue
            if typ != 1:
                return
            veto = buf[17]
            with LOCK:
                sensor = STATE.get("last_sensor")
                STATE["veto"] = veto
                STATE["heartbeats"] += 1
                edge = int(STATE.get("_edge_int") or 0)
                skill = EXPLORER.step(sensor, veto, edge)
                STATE["skill"] = NAMES.get(skill, "idle")
            t_ns = time.time_ns()
            reply = b"VHB1" + bytes([2]) + buf[5:9] + struct.pack("<Q", t_ns) + bytes([skill])
            cmds = pop_cmds()
            sent = 0
            try:
                conn.sendall(reply)
                for kind, payload in cmds:
                    hdr = b"VHB1" + bytes([3]) + buf[5:9] + struct.pack("<Q", t_ns) + bytes([kind])
                    conn.sendall(hdr + struct.pack("<I", len(payload)) + payload)
                    sent += 1
            except OSError:
                for kind, payload in cmds[sent:]:
                    queue_cmd(kind, payload)
                return
    except OSError:
        return


def _read_n(conn: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


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

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or "0")
        return self.rfile.read(n) if n else b""

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/status", "/healthz"):
            with LOCK:
                body = dict(STATE)
                body.pop("_edge_int", None)
            self._json(body)
            return
        if path == "/faces":
            self._json({"faces": FACE_DB.list()})
            return
        if path == "/ui":
            html = UI_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        self.send_error(404)

    def do_DELETE(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path != "/faces":
            self.send_error(404)
            return
        qs = parse_qs(u.query)
        try:
            fid = int((qs.get("id") or ["0"])[0])
        except ValueError:
            self._json({"ok": False, "error": "id"}, 400)
            return
        self._json({"ok": FACE_DB.delete(fid)})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        raw = self._body()
        try:
            data = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            data = {}
        if path == "/faces":
            name = str(data.get("name") or "")
            img = b""
            if data.get("image_b64"):
                img = base64.b64decode(data["image_b64"])
            elif LAST_JPEG["face"] or LAST_JPEG["nav"]:
                img = LAST_JPEG["face"] or LAST_JPEG["nav"]
            try:
                self._json(FACE_DB.enroll(name, img))
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, 400)
            return
        if path == "/think":
            on = bool(data.get("on", True))
            with LOCK:
                STATE["thinking"] = on
            queue_cmd(CMD_FACEUI, b"thinking|" if on else b"idle|")
            self._json({"thinking": on})
            return
        if path == "/say":
            text = str(data.get("text") or "")
            with LOCK:
                STATE["thinking"] = True
            queue_cmd(CMD_FACEUI, b"thinking|")
            pcm = VOICE.tts(text) if text else tone()
            queue_cmd(CMD_SPEAK, pcm)
            queue_cmd(CMD_FACEUI, b"idle|")
            with LOCK:
                STATE["thinking"] = False
                STATE["last_reply"] = text
            self._json({"ok": True, "bytes": len(pcm)})
            return
        self.send_error(404)


UI_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>victor hub</title>
<style>body{font-family:sans-serif;max-width:40rem;margin:2rem auto}</style></head>
<body><h1>victor hub</h1><p>Face names (local SQLite, not OpenAI).</p>
<form onsubmit="enroll(event)"><input name="name" placeholder="name" required>
<button>enroll last frame</button></form>
<pre id="out"></pre>
<script>
async function enroll(e){e.preventDefault();
 const name=e.target.name.value;
 const r=await fetch('/faces',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({name})});
 document.getElementById('out').textContent=await r.text();
}
fetch('/faces').then(r=>r.json()).then(j=>document.getElementById('out').textContent=JSON.stringify(j,null,2));
</script></body></html>
"""


def main() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        print("openai key present on hub only", flush=True)
    if EDGE.path:
        print(f"edge model {EDGE.path}", flush=True)
    else:
        print("edge model absent; votes unknown (IR cliffs still win)", flush=True)
    host = "0.0.0.0"
    udp_sensor = int(os.environ.get("HUB_SENSOR_PORT", "7502"))
    udp_audio = int(os.environ.get("HUB_AUDIO_PORT", "7501"))
    udp_video = int(os.environ.get("HUB_VIDEO_PORT", "7500"))
    tcp_port = int(os.environ.get("HUB_SKILL_PORT", "7443"))
    http_port = int(os.environ.get("HUB_HTTP_PORT", "8080"))
    threading.Thread(target=voice_loop, daemon=True).start()
    threading.Thread(target=udp_loop, args=(host, udp_sensor), daemon=True).start()
    threading.Thread(target=udp_loop, args=(host, udp_audio), daemon=True).start()
    threading.Thread(target=udp_loop, args=(host, udp_video), daemon=True).start()
    threading.Thread(target=tcp_loop, args=(host, tcp_port), daemon=True).start()
    httpd = ThreadingHTTPServer((host, http_port), Status)
    print(f"hub HTTP {http_port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
