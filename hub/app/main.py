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
from face_id import FaceID, is_identity_question, person_context
import voice as voice_mod
from protocol import FLAG_FACE, TYPE_AUDIO, TYPE_SENSOR, TYPE_VIDEO, decode, unpack_sensor
from safety import classical_vote
from voice import MIN_UTTERANCE_BYTES, Voice, _wav_wrap, speech_like, tone

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
    "voice_busy": False,
}
_WINDOW = []
LOCK = threading.Lock()
EXPLORER = Explorer()
FACE_DB = FaceDB()  # HUB_FACE_DB or /app/data/faces.db (persistent volume)
FACE_ID = FaceID(FACE_DB)
FACE_GREET_S = float(os.environ.get("HUB_FACE_GREET_S", "600") or 600)
VOICE = Voice()
EDGE = Edge()
PENDING: list[tuple[int, bytes]] = []
UTTERANCES: queue.Queue[bytes] = queue.Queue(maxsize=2)
LAST_JPEG = {"nav": b"", "face": b""}
LAST_FACE_SPOKEN = {"name": "", "t": 0.0}
# Mic test recorder (POST /record). While it runs the VAD/OpenAI turn is paused so
# the file is the continuous robot stream, not VAD-gated pieces.
REC = {"active": False, "buf": bytearray()}
REC_DIR = "/app/data/rec"


class SeqDedupe:
    """The agent sends every media packet on UDP and again on the TCP link.

    Feeding both copies to the VAD doubles and interleaves the stream (half the
    20 ms packets play twice, out of order), which sounds like a buzzing comb.
    Keep the first copy of each seq. A seq far below the newest one means the
    agent restarted and its counter began again.
    """

    def __init__(self, window: int = 4096) -> None:
        self.window = window
        self.seen: set[int] = set()
        self.newest = -1

    def first(self, seq: int) -> bool:
        if self.newest >= 0 and seq < self.newest - self.window:
            self.seen.clear()
            self.newest = -1
        if seq in self.seen:
            return False
        self.seen.add(seq)
        if seq > self.newest:
            self.newest = seq
        if len(self.seen) > self.window * 2:
            floor = self.newest - self.window
            self.seen = {s for s in self.seen if s >= floor}
        return True


AUDIO_SEQ = SeqDedupe()
VIDEO_SEQ = SeqDedupe(window=128)  # ~8 s of video: an agent restart resets seq
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
            if not AUDIO_SEQ.first(hdr.seq):
                STATE["audio_dup"] = STATE.get("audio_dup", 0) + 1
                return
            STATE["audio"] += 1
            if REC["active"]:
                REC["buf"] += payload
                return
        _on_audio(payload)
        return
    if hdr.type == TYPE_VIDEO:
        with LOCK:
            if not VIDEO_SEQ.first(hdr.seq):
                return
            STATE["video"] += 1
        if hdr.flags & FLAG_FACE:
            LAST_JPEG["face"] = payload
            _on_face_frame(payload)
        else:
            LAST_JPEG["nav"] = payload
            _on_nav_frame(payload)


def _enqueue_utterance(utt: bytes) -> None:
    while True:
        try:
            UTTERANCES.put_nowait(utt)
            return
        except queue.Full:
            try:
                UTTERANCES.get_nowait()
                print("voice queue dropped oldest", flush=True)
            except queue.Empty:
                print("voice queue full; dropped newest", flush=True)
                return


def _on_audio(pcm: bytes) -> None:
    with LOCK:
        busy = bool(STATE.get("voice_busy"))
    if busy:
        return
    utt = VOICE.push(pcm)
    with LOCK:
        STATE["audio_rms"] = getattr(VOICE, "last_rms", 0)
        STATE["noise_rms"] = int(getattr(VOICE, "noise", 0))
    if not utt:
        return
    if len(utt) < MIN_UTTERANCE_BYTES:
        print(f"voice skip short {len(utt)} bytes", flush=True)
        return
    if not speech_like(utt):
        print(f"voice skip rumble {len(utt)} bytes rms={VOICE.last_rms} noise={int(VOICE.noise)}", flush=True)
        return
    if not begin_think("vad"):
        return
    FACE_ID.kick()
    print(f"voice utterance {len(utt)} bytes rms={VOICE.last_rms} noise={int(VOICE.noise)}", flush=True)
    try:
        os.makedirs("/app/data", exist_ok=True)
        open("/app/data/last.wav", "wb").write(_wav_wrap(utt))
    except OSError as exc:
        print(f"voice wav save failed {exc}", flush=True)
    _enqueue_utterance(utt)


THINK_REFRESH_S = float(os.environ.get("HUB_THINK_REFRESH", "4"))
THINK_MAX_S = float(os.environ.get("HUB_THINK_MAX", "120"))
THINK = {"t0": 0.0, "sent": 0.0, "why": ""}


def _ms() -> str:
    """UTC with ms; the agent logs the same format so the two line up."""
    t = time.time()
    return time.strftime("%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


def begin_think(why: str) -> bool:
    """Start one reply turn: busy (mic closed), thinking face on the robot.

    False if a turn is already running. Every True must be paired with
    end_think (voice_loop / speak_turn do it in finally)."""
    now = time.time()
    with LOCK:
        if STATE.get("voice_busy"):
            return False
        STATE["voice_busy"] = True
        STATE["thinking"] = True
        THINK.update(t0=now, sent=now, why=why)
        PENDING.append((CMD_FACEUI, b"thinking|"))
    print(f"think on {_ms()} why={why}", flush=True)
    return True


def end_think(audio: bytes = b"", why: str = "done", show: bytes = b"") -> None:
    """Finish the turn. The speech goes out before idle in the same batch, so
    the robot drops the thinking face exactly when playback starts; with no
    audio (error, empty transcript) idle clears it at once. No keepalive can
    slip in after this: thinking is cleared under the same lock."""
    with LOCK:
        held = time.time() - THINK["t0"] if THINK["t0"] else 0.0
        STATE["thinking"] = False
        STATE["voice_busy"] = False
        THINK.update(t0=0.0, sent=0.0, why="")
        if show:  # e.g. name|Ann: replaces thinking, shown while it speaks
            PENDING.append((CMD_FACEUI, show))
        if audio:
            PENDING.append((CMD_SPEAK, audio))
        if not show:
            PENDING.append((CMD_FACEUI, b"idle|"))
    print(f"think off {_ms()} why={why} held={held:.1f}s speak={len(audio)}", flush=True)


def think_keepalive(now: float | None = None) -> bool:
    """Re-send thinking every THINK_REFRESH_S while a turn runs. The agent
    drops a thinking face that is not refreshed (hub restart, lost idle), so
    a slow TTS stays covered and nothing can stay stuck. After THINK_MAX_S the
    turn is considered hung: stop refreshing and let the robot clear."""
    now = time.time() if now is None else now
    with LOCK:
        if not STATE.get("thinking") or not THINK["t0"]:
            return False
        if now - THINK["t0"] > THINK_MAX_S:
            return False
        if now - THINK["sent"] < THINK_REFRESH_S:
            return False
        if any(k == CMD_FACEUI and p == b"thinking|" for k, p in PENDING):
            return False  # robot offline; one queued refresh is enough
        THINK["sent"] = now
        PENDING.append((CMD_FACEUI, b"thinking|"))
    return True


def think_loop() -> None:
    while True:
        time.sleep(0.5)
        think_keepalive()


def reply_turn(pcm: bytes) -> tuple[str, str, bytes]:
    """STT -> chat (web search) -> TTS, timed. Thinking stays on throughout."""
    t = time.time()
    text = VOICE.transcribe(pcm)
    if text and FACE_ID.ready() and is_identity_question(text):
        FACE_ID.wait_fresh(max_age=15, timeout=float(os.environ.get("HUB_FACE_WAIT_S", "4") or 4))
    t_stt = time.time()
    reply = VOICE.chat(text) if text else ""
    t_chat = time.time()
    audio = VOICE.tts(reply) if reply else b""
    t_tts = time.time()
    print(
        f"voice timing end={_ms()}  stt={int((t_stt - t) * 1000)}ms chat={int((t_chat - t_stt) * 1000)}ms "
        f"tts={int((t_tts - t_chat) * 1000)}ms via={getattr(VOICE, 'last_via', '')}",
        flush=True,
    )
    return text, reply, audio


def voice_loop() -> None:
    """One OpenAI turn at a time. Mic stays closed until the reply is queued."""
    while True:
        run_turn(UTTERANCES.get())


def run_turn(pcm: bytes) -> None:
    """One reply turn; thinking is always cleared, even if a call raises."""
    with LOCK:
        running = bool(STATE.get("thinking"))
    if not running:  # turn was not opened by _on_audio (tests, /record)
        begin_think("queue")
    text, reply, audio, why = "", "", b"", "done"
    try:
        text, reply, audio = reply_turn(pcm)
        if not audio:
            why = "no-reply" if text else ("dropped" if getattr(VOICE, "last_drop", "") else "no-transcript")
    except Exception as exc:  # noqa: BLE001 - never leave the face thinking
        why = f"error {type(exc).__name__}"
        print(f"voice turn failed {exc!r}", flush=True)
    finally:
        VOICE.thinking = False
        with LOCK:
            STATE["last_transcript"] = text
            STATE["last_reply"] = reply
            STATE["last_searched"] = bool(getattr(VOICE, "last_searched", False))
            STATE["last_via"] = getattr(VOICE, "last_via", "")
            STATE["last_chat_ms"] = getattr(VOICE, "last_chat_ms", 0)
            STATE["last_lang"] = getattr(VOICE, "last_lang", "")
            STATE["last_drop"] = getattr(VOICE, "last_drop", "")
        end_think(audio, why)
    print(f"voice transcript={text!r} lang={getattr(VOICE, 'last_lang', '')} reply={reply!r} speak={len(audio)}", flush=True)


def speak_turn(text: str, why: str, show: bytes = b"") -> int:
    """TTS outside a voice turn (/say, face greeting) under the same thinking
    face. If a voice turn is running it owns the face; just queue the audio."""
    own = begin_think(why)
    pcm = b""
    try:
        pcm = VOICE.tts(text) if text else tone()
    except Exception as exc:  # noqa: BLE001
        print(f"speak {why} failed {exc!r}", flush=True)
        pcm = b""
    finally:
        if own:
            end_think(pcm, why, show)
        elif pcm:
            queue_cmd(CMD_SPEAK, pcm)
    return len(pcm)


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
    """Face-size frames feed the NVIDIA face-to-name matcher (background)."""
    FACE_ID.on_frame(jpeg)


def _person_context() -> str:
    return person_context(FACE_ID.present(), FACE_ID.min_conf)


def _on_face_result(res: dict) -> None:
    """A confident match: show the name and greet, at most once per FACE_GREET_S."""
    name = res.get("name")
    if not name or float(res.get("confidence") or 0) < FACE_ID.min_conf:
        return
    with LOCK:
        STATE["face"] = {"name": name, "score": round(float(res["confidence"]), 2)}
    now = time.time()
    if LAST_FACE_SPOKEN["name"] == name and now - LAST_FACE_SPOKEN["t"] < FACE_GREET_S:
        return
    with LOCK:
        busy = bool(STATE.get("voice_busy"))
    if busy:
        return  # don't talk over a reply turn; try on the next result
    LAST_FACE_SPOKEN["name"] = name
    LAST_FACE_SPOKEN["t"] = now
    show = f"name|{name}".encode()
    if VOICE.key:
        threading.Thread(target=speak_turn, args=(f"Hi, {name}!", "face", show), daemon=True).start()
    else:
        queue_cmd(CMD_FACEUI, show)
        queue_cmd(CMD_SPEAK, tone(660, 250))


voice_mod.PERSON_CONTEXT = _person_context
FACE_ID.on_result = _on_face_result


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
            body["voice"] = {
                "tts_voice": VOICE.voice,
                "tts_model": VOICE.tts_model,
                "vector_fx": os.environ.get("HUB_VOICE_VECTOR", "1").strip().lower() not in ("0", "false", "no", "off", ""),
                "langs": VOICE.langs,
                "stt_language": VOICE.stt_language or "auto",
            }
            body["person"] = FACE_ID.present()
            self._json(body)
            return
        if path == "/faces":
            self._json({"faces": FACE_DB.list()})
            return
        if path == "/faces/img":
            try:
                fid = int((parse_qs(urlparse(self.path).query).get("id") or ["0"])[0])
            except ValueError:
                fid = 0
            img = FACE_DB.image(fid)
            if not img:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(img)))
            self.end_headers()
            self.wfile.write(img)
            return
        if path == "/frame":
            kind = (parse_qs(urlparse(self.path).query).get("kind") or ["face"])[0]
            img = LAST_JPEG.get("face" if kind != "nav" else "nav") or b""
            if not img:
                self.send_error(404, "no frame yet")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(img)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(img)
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
        if qs.get("name"):
            self._json({"ok": True, "deleted": FACE_DB.delete_name(qs["name"][0])})
            return
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
            self._json(*enroll(data))
            return
        if path == "/record":
            self._json(*record(data))
            return
        if path == "/think":
            on = bool(data.get("on", True))
            if on:
                ok = begin_think("http")
            else:
                end_think(why="http")
                ok = True
            self._json({"thinking": on, "ok": ok})
            return
        if path == "/say":
            text = str(data.get("text") or "")
            n = speak_turn(text, "say")
            with LOCK:
                STATE["last_reply"] = text
            self._json({"ok": n > 0, "bytes": n})
            return
        self.send_error(404)


def enroll(data: dict) -> tuple[dict, int]:
    """POST /faces {"name": "Ann", "count": 3, "gap": 1.5} enrolls `count` live
    face frames taken `gap` s apart (or one "image_b64"). With the NVIDIA key set,
    each frame is first checked for a visible person; frames without one are
    skipped, so standing out of view enrolls nothing."""
    from face_id import clean_name

    name = clean_name(str(data.get("name") or ""))
    if not name:
        return {"ok": False, "error": "name required"}, 400
    if data.get("image_b64"):
        try:
            frames = [base64.b64decode(data["image_b64"])]
        except (ValueError, TypeError):
            return {"ok": False, "error": "image_b64"}, 400
    else:
        try:
            count = max(1, min(int(data.get("count") or 3), 6))
            gap = max(0.5, min(float(data.get("gap") or 1.5), 5.0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "count/gap"}, 400
        frames = []
        last = b""
        deadline = time.time() + count * gap + 5
        while len(frames) < count and time.time() < deadline:
            img = LAST_JPEG["face"]
            if img and img is not last and (not frames or time.time() - frames[-1][1] >= gap):
                frames.append((img, time.time()))
                last = img
            time.sleep(0.1)
        frames = [f for f, _ in frames]
    if not frames:
        return {"ok": False, "error": "no camera frame (is the robot streaming video?)"}, 409
    stored, skipped, checks = [], 0, []
    for img in frames:
        if FACE_ID.ready():
            res = FACE_ID.recognize(img, [])  # no gallery: just "is a person visible?"
            checks.append({"person": res["person"], "ms": res["ms"], "error": res["error"][:80]})
            if res["error"] or not res["person"]:
                skipped += 1
                continue
        stored.append(FACE_DB.enroll(name, img)["id"])
    ok = bool(stored)
    out = {"ok": ok, "name": name, "stored": len(stored), "ids": stored, "skipped": skipped, "checks": checks}
    if not ok:
        out["error"] = "no person visible in the frames; stand 0.5-1 m in front of the robot, face it, and retry"
    print(f"face enroll name={name!r} stored={len(stored)} skipped={skipped}", flush=True)
    if ok:
        FACE_ID.kick()
    return out, 200 if ok else 422


def _safe_name(name: str) -> str:
    keep = "".join(c for c in name if c.isalnum() or c in "-_.")
    return keep.strip(".")[:64] or time.strftime("rec-%Y%m%d-%H%M%S")


def record(data: dict) -> tuple[dict, int]:
    """Record the raw robot mic stream for N seconds to /app/data/rec/<name>.wav.

    {"secs": 20, "name": "baseline", "transcribe": true}. Transcription uses the
    same hub STT as a voice turn (OpenAI, key on the hub only).
    """
    try:
        secs = float(data.get("secs") or 10)
    except (TypeError, ValueError):
        return {"ok": False, "error": "secs"}, 400
    secs = max(1.0, min(secs, 120.0))
    name = _safe_name(str(data.get("name") or ""))
    with LOCK:
        if REC["active"]:
            return {"ok": False, "error": "busy"}, 409
        REC["active"] = True
        REC["buf"] = bytearray()
    try:
        time.sleep(secs)
    finally:
        with LOCK:
            REC["active"] = False
            pcm = bytes(REC["buf"])
            REC["buf"] = bytearray()
    os.makedirs(REC_DIR, exist_ok=True)
    path = os.path.join(REC_DIR, name + ".wav")
    with open(path, "wb") as f:
        f.write(_wav_wrap(pcm))
    out = {"ok": True, "path": path, "seconds": round(len(pcm) / 2 / 16000, 2), "bytes": len(pcm)}
    if data.get("transcribe") and pcm:
        out["transcript"] = VOICE.transcribe(pcm)
    return out, 200


UI_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>victor hub - faces</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:sans-serif;max-width:44rem;margin:1.5rem auto;padding:0 1rem}
img.live{width:100%;max-width:640px;border:1px solid #888}.refs img{height:96px;margin:2px}
.row{margin:.4rem 0}button{padding:.4rem .8rem}</style></head>
<body><h1>victor hub - faces</h1>
<p>Live robot camera (1 fps). Stand 0.5-1 m in front of Vector, face it, good light.</p>
<img class="live" id="live" alt="no camera frame yet">
<form onsubmit="enroll(event)" class="row"><input name="name" placeholder="your name" required>
<button>enroll (3 photos, ~5 s)</button></form>
<div class="row" id="who"></div>
<pre id="out"></pre><div id="list"></div>
<script>
function tick(){document.getElementById('live').src='/frame?kind=face&t='+Date.now();
 fetch('/status').then(r=>r.json()).then(j=>{const p=j.person||{};
  document.getElementById('who').textContent='recognised: '+(p.present_name||'nobody')+
   ' conf '+p.confidence+' age '+p.age_s+'s person='+p.present_person+(p.error?' error: '+p.error:'');});}
setInterval(tick,1000);tick();
async function enroll(e){e.preventDefault();const name=e.target.name.value;
 document.getElementById('out').textContent='taking photos...';
 const r=await fetch('/faces',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({name,count:3,gap:1.5})});
 document.getElementById('out').textContent=await r.text();list();}
async function del(id){await fetch('/faces?id='+id,{method:'DELETE'});list();}
function list(){fetch('/faces').then(r=>r.json()).then(j=>{const d=document.getElementById('list');d.innerHTML='';
 for(const f of j.faces){const s=document.createElement('div');s.className='refs';
  s.innerHTML=(f.photo?'<img src="/faces/img?id='+f.id+'">':'(no photo)')+' '+f.name+' #'+f.id+
   ' <button onclick="del('+f.id+')">delete</button>';d.appendChild(s);}});}
list();
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
    threading.Thread(target=FACE_ID.loop, daemon=True).start()
    if FACE_ID.ready():
        print(f"face id on: model={FACE_ID.model} key=${FACE_ID.key_var} (value not logged)", flush=True)
    else:
        print(f"face id off: set ${FACE_ID.key_var} (and HUB_FACE_ID=1) for face-to-name", flush=True)
    threading.Thread(target=think_loop, daemon=True).start()
    threading.Thread(target=udp_loop, args=(host, udp_sensor), daemon=True).start()
    threading.Thread(target=udp_loop, args=(host, udp_audio), daemon=True).start()
    threading.Thread(target=udp_loop, args=(host, udp_video), daemon=True).start()
    threading.Thread(target=tcp_loop, args=(host, tcp_port), daemon=True).start()
    httpd = ThreadingHTTPServer((host, http_port), Status)
    print(f"hub HTTP {http_port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
