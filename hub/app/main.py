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
from face_id import NO_CONTEXT, FaceID, is_identity_question, person_context
import intents as intents_mod
import router
import voice as voice_mod
import wake as wake_mod
from api import Api
from session import Session
from protocol import FLAG_FACE, FLAG_ROBOT_NOISE, TYPE_AUDIO, TYPE_SENSOR, TYPE_VIDEO, decode, unpack_sensor
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
VOICE = Voice()
EDGE = Edge()
PENDING: list[tuple[int, bytes]] = []
UTTERANCES: queue.Queue[bytes] = queue.Queue(maxsize=2)
# Wake word (HUB_WAKE, default on). Asleep, an utterance still gets the normal
# STT, but the transcript is only checked for "Hey Vector": a miss is dropped
# silently (no router, chat, TTS or thinking face). Awake until "Stop Vector".
SESSION = Session()
ASLEEP = {"heard": 0, "ignored": 0, "woke": 0, "last": ""}
WAKE_CHIME = os.environ.get("HUB_WAKE_CHIME", "1").strip().lower() not in ("0", "false", "no", "off", "")
LAST_JPEG = {"nav": b"", "face": b""}
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
CMD_ACTION = 4  # payload: action name; the agent's runner executes it under the on-robot veto


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
        _button_events(sensor)
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
        _on_audio(payload, bool(hdr.flags & FLAG_ROBOT_NOISE))
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


BUTTON = {"last": None, "presses": 0, "last_at": 0.0, "last_action": ""}


def _button_events(sensor: dict) -> None:
    """SENSOR carries the robot's running press count; each increase is one
    button_press (a lost UDP packet only delays it). A drop = agent restart."""
    n = sensor.get("button_presses")
    if n is None:
        return
    with LOCK:
        last = BUTTON["last"]
        BUTTON["last"] = n
    if last is None or n == last:
        return
    new = (n - last) & 0xFFFF
    if n < last and new > 50:  # agent restarted (counter back near 0), not 65k presses
        return
    for _ in range(min(new, 3)):
        button_press()


def button_press() -> str:
    """Backpack press: asleep -> wake (same cue as 'Hey Vector'). Awake: logged only."""
    with LOCK:
        BUTTON["presses"] += 1
        BUTTON["last_at"] = time.time()
    if SESSION.enabled and SESSION.state != "awake":
        SESSION.wake("button")
        cue_wake()
        action = "wake"
    else:
        action = "ignored (awake)" if SESSION.enabled else "ignored (wake word off)"
    BUTTON["last_action"] = action
    print(f"button_press -> {action}", flush=True)
    return action


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


def _on_audio(pcm: bytes, robot_noise: bool = False) -> None:
    with LOCK:
        busy = bool(STATE.get("voice_busy"))
    if busy:
        return
    utt = VOICE.push(pcm, robot_noise)
    with LOCK:
        STATE["audio_rms"] = getattr(VOICE, "last_rms", 0)
        STATE["noise_rms"] = int(getattr(VOICE, "noise", 0))
        STATE["vad"] = {"rejected": dict(VOICE.rejected), "robot_noise_frames": VOICE.robot_noise_frames}
    if not utt:
        return
    if len(utt) < MIN_UTTERANCE_BYTES:
        print(f"voice skip short {len(utt)} bytes", flush=True)
        return
    if not speech_like(utt):
        print(f"voice skip rumble {len(utt)} bytes rms={VOICE.last_rms} noise={int(VOICE.noise)}", flush=True)
        return
    if not SESSION.listening():
        _enqueue_utterance(("asleep", utt))  # STT + wake check only; no thinking face
        return
    if not begin_think("vad"):
        return
    print(f"voice utterance {len(utt)} bytes rms={VOICE.last_rms} noise={int(VOICE.noise)}", flush=True)
    try:
        os.makedirs("/app/data", exist_ok=True)
        open("/app/data/last.wav", "wb").write(_wav_wrap(utt))
    except OSError as exc:
        print(f"voice wav save failed {exc}", flush=True)
    _enqueue_utterance(utt)


def chime() -> bytes:
    """Soft two-note rising chime (16 kHz PCM), the wake cue."""
    import math as _m
    import struct as _s

    out = bytearray()
    for hz, ms in ((660.0, 90), (880.0, 120)):
        n = 16000 * ms // 1000
        for i in range(n):
            env = min(1.0, i / 160, (n - i) / 400)
            out += _s.pack("<h", int(3500 * env * _m.sin(2 * _m.pi * hz * i / 16000)))
    return bytes(out)


def cue_wake() -> None:
    queue_cmd(CMD_FACEUI, b"anim|lookatme")  # eyes widen / look up at you
    if WAKE_CHIME:
        queue_cmd(CMD_SPEAK, VOLUME.apply(chime()))


def cue_sleep() -> None:
    queue_cmd(CMD_FACEUI, b"anim|goodnight")


def asleep_turn(pcm: bytes) -> dict:
    """One utterance while asleep: STT, then only the wake check. A hit opens
    the session (cue); words after the wake phrase run at once as turn one."""
    if SESSION.listening():  # woke while this waited: a normal turn
        run_turn(pcm)
        return {"hit": False, "queued": True}
    text = VOICE.transcribe(pcm)
    hit, rest = wake_mod.match_wake(text) if text else (False, "")
    with LOCK:
        ASLEEP["heard"] += 1 if text else 0
        ASLEEP["ignored"] += 0 if hit or not text else 1
        ASLEEP["woke"] += 1 if hit else 0
        ASLEEP["last"] = text[:80]
    if not hit:
        if text:
            print(f"asleep ignore text={text!r}", flush=True)
        return {"hit": False, "text": text}
    print(f"asleep WAKE text={text!r} rest={rest!r}", flush=True)
    SESSION.wake("phrase")
    cue_wake()
    if len(rest) >= 2:
        run_turn(pcm, text=text)  # "Hey Vector, what time is it?" -> answered now
    return {"hit": True, "text": text, "rest": rest}


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
            PENDING.append((CMD_SPEAK, VOLUME.apply(audio)))
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
        if SESSION.check_idle():  # HUB_SESSION_IDLE_S (0 = never)
            cue_sleep()


def reply_turn(pcm: bytes, text: str | None = None) -> tuple[str, str, bytes]:
    """STT -> stock command (no chat) or chat (web search) -> TTS, timed.
    Thinking stays on throughout. A matched command leaves its face clip and
    robot action in TURN_OUT for run_turn to send after the reply."""
    t = time.time()
    if text is None:
        text = VOICE.transcribe(pcm)
    TURN_PERSON["ctx"] = ""
    TURN_OUT.update(show=b"", action="", intent="")
    VOICE.last_via = ""
    t_r = time.time()
    if text and SESSION.enabled:
        if wake_mod.is_session_stop(text):
            return session_stop(text)
        bare = wake_mod.strip_wake(text)
        if not bare:  # just "Hey Vector" again inside the session
            cue_wake()
            SESSION.touch()
            return text, "", b""
        text = bare
    intent, how = (None, "off")
    confirm = SSH_CONFIRM["until"] > time.time()
    if text:
        SSH_CONFIRM["until"] = 0.0  # one answer only (an empty/noise turn keeps the window)
    if text and INTENTS_ON and confirm and intents_mod.is_yes(text):
        # the spoken yes to "Did you want me to turn SSH off?"
        intent, how = intents_mod.Intent(name="intent_system_ssh_disable", lang=SSH_CONFIRM["lang"], arg="confirmed",
                                         text=intents_mod.normalise(text)), "confirm"
        print(f"ssh off confirmed by {text!r}", flush=True)
    elif text and INTENTS_ON and is_identity_question(text):
        intent, how = router.fast(text), "identity"  # face path; no router call
    elif text and INTENTS_ON:
        intent, how = router.route(VOICE.api, text, getattr(VOICE, "last_lang", ""), bool(VOICE.key))
    TURN_OUT["route"] = how
    if intent and intent.name != "intent_names_username_extend" and is_identity_question(text):
        intent = None  # identity questions keep the face-ID path
    t_face = time.time()
    if text and intent is None and is_identity_question(text):
        # The only place a camera frame goes to the AI during voice: one call, this turn only.
        res = FACE_ID.identify()
        TURN_PERSON["ctx"] = person_context(res, FACE_ID.min_conf)
        if res.get("name") and float(res.get("confidence") or 0) >= FACE_ID.min_conf:
            with LOCK:
                STATE["face"] = {"name": res["name"], "score": round(float(res["confidence"]), 2)}
    t_stt = time.time()
    try:
        if intent is not None:
            reply = run_intent(intent)
            VOICE.last_via = "intent"
        else:
            if text:
                print(f"intent none -> chat text={text!r} norm={intents_mod.normalise(text)!r}", flush=True)
            reply = VOICE.chat(text) if text else ""
    finally:
        TURN_PERSON["ctx"] = ""
    t_chat = time.time()
    reply = voice_mod.own_name(reply)
    SESSION.add(text, reply, getattr(VOICE, "last_lang", ""))
    audio = VOICE.tts(reply) if reply else b""
    t_tts = time.time()
    print(
        f"voice timing end={_ms()}  stt={int((t_r - t) * 1000)}ms route={int((t_face - t_r) * 1000)}ms/{TURN_OUT.get('route', '')} face={int((t_stt - t_face) * 1000)}ms chat={int((t_chat - t_stt) * 1000)}ms "
        f"tts={int((t_tts - t_chat) * 1000)}ms via={getattr(VOICE, 'last_via', '')}",
        flush=True,
    )
    return text, reply, audio


def session_stop(text: str) -> tuple[str, str, bytes]:
    """'Stop Vector': stop motion/wander, short ack, asleep cue, back to asleep."""
    lang = wake_mod.stop_lang(text, getattr(VOICE, "last_lang", "") or "en")
    reply = intents_mod.say("session_stop", lang)
    SESSION.sleep("phrase")
    SSH_CONFIRM["until"] = 0.0
    TURN_OUT.update(show=b"anim|goodnight", action="stop", intent="session_stop")
    print(f"session stop by {text!r} lang={lang}", flush=True)
    return text, reply, VOICE.tts(reply)


# ------------------------------------------------------------ stock commands
INTENTS_ON = os.environ.get("HUB_INTENTS", "1").strip().lower() not in ("0", "false", "no", "off")
TURN_OUT = {"show": b"", "action": "", "intent": ""}
PHOTO_DIR = os.environ.get("HUB_PHOTO_DIR") or "/app/data/photos"


class Volume:
    """Speech gain on the hub, levels 1..5 (default 5 = loudest, HUB_VOLUME_DEFAULT).

    TTS arrives peak-normalised to -1 dBFS, so plain gain above 1.0 only hard-clips.
    Level 5 adds ~+5 dB of loudness with a soft limiter instead: samples above the
    knee are compressed smoothly towards full scale, nothing is clipped flat."""

    GAINS = {1: 0.25, 2: 0.45, 3: 0.7, 4: 1.0, 5: 2.0}
    KNEE = 0.55  # fraction of full scale where the limiter starts bending

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("HUB_VOLUME_FILE") or "/app/data/volume.txt"
        try:
            self.level = max(1, min(5, int(os.environ.get("HUB_VOLUME_DEFAULT", "5"))))
        except ValueError:
            self.level = 5
        try:
            self.level = max(1, min(5, int(open(self.path).read().strip())))
        except (OSError, ValueError):
            pass

    def set(self, level: int) -> int:
        self.level = max(1, min(5, int(level)))
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                f.write(str(self.level))
        except OSError as exc:
            print(f"volume save failed {exc!r}", flush=True)
        return self.level

    def _lut(self, g: float) -> list[int]:
        """|sample| 0..32768 -> output magnitude for gain g (pure Python, no numpy in the voice thread)."""
        import math
        k = self.KNEE
        out = []
        for i in range(32769):
            a = i / 32768.0 * g
            if g > 1.0:
                if a > k:
                    a = k + (1 - k) * math.tanh((a - k) / (1 - k))
                a *= 0.97
            out.append(min(32767, int(round(a * 32768.0))))
        return out

    def apply(self, pcm: bytes) -> bytes:
        g = self.GAINS.get(self.level, 1.0)
        if g == 1.0 or not pcm:
            return pcm
        if not hasattr(self, "_luts"):
            self._luts = {}
        lut = self._luts.get(g)
        if lut is None:
            lut = self._luts[g] = self._lut(g)
        from array import array
        x = array("h")
        x.frombytes(pcm[: len(pcm) // 2 * 2])
        for i, v in enumerate(x):
            x[i] = lut[v] if v >= 0 else -lut[-v]
        return x.tobytes()


VOLUME = Volume()


class Timer:
    """One kitchen timer. fire(lang) runs on a daemon thread when it ends."""

    def __init__(self):
        self.lock = threading.Lock()
        self.end = 0.0
        self.lang = "en"
        self.gen = 0

    def start(self, seconds: int, lang: str, fire) -> None:
        with self.lock:
            self.gen += 1
            gen, self.end, self.lang = self.gen, time.time() + seconds, lang

        def run():
            time.sleep(seconds)
            with self.lock:
                if gen != self.gen or not self.end:
                    return
                self.end = 0.0
            fire(lang)

        threading.Thread(target=run, daemon=True).start()

    def left(self) -> int:
        with self.lock:
            return max(0, int(round(self.end - time.time()))) if self.end else 0

    def cancel(self) -> bool:
        with self.lock:
            was = bool(self.end)
            self.end, self.gen = 0.0, self.gen + 1
            return was


TIMER = Timer()


def _timer_fire(lang: str) -> None:
    print(f"intent timer done {_ms()}", flush=True)
    speak_turn(intents_mod.say("timer_done", lang), "timer", b"anim|hello")


def _on_charger() -> bool | None:
    with LOCK:
        s = STATE.get("last_sensor")
    return None if not s else bool(s.get("on_charger"))


def _lang(intent) -> str:
    lang = getattr(VOICE, "last_lang", "") or intent.lang
    return lang if lang in ("en", "de", "fa") else intent.lang


# ------------------------------------------------------------ SSH by voice
# Owner's request (10 Oct 2026): SSH on the robot is toggled by voice only, no face
# check. Turning it OFF needs an exact phrase ("disable SSH", "SSH aus", ...) or the
# router >= 0.85 with SSH + an off verb in the transcript; anything else makes Vector
# ask "Did you want me to turn SSH off?" and only a yes within SSH_CONFIRM_S does it.
SSH_CONFIRM_S = 14.0  # ~2 s of the question being spoken + ~10 s to answer
SSH_CONFIRM = {"until": 0.0, "lang": "en"}
SSH_DISABLE_MIN_CONF = 0.85
SSH_WAIT_S = 4.0


def ssh_disable_ok(norm_text: str, raw_text: str = "") -> bool:
    text = raw_text or norm_text
    exact = intents_mod.match(text)
    if exact is not None and exact.name == "intent_system_ssh_disable":
        return True
    return (router.LAST.get("how") == "llm" and float(router.LAST.get("conf") or 0) >= SSH_DISABLE_MIN_CONF
            and intents_mod.ssh_off_explicit(text))


def _ssh_set(on: bool, lang: str) -> str:
    """Ask the agent to switch SSH, wait for its sensor flag to agree, say the result."""
    queue_cmd(CMD_ACTION, b"ssh_on" if on else b"ssh_off")
    print(f"ssh voice -> {'on' if on else 'off'}", flush=True)
    deadline = time.time() + SSH_WAIT_S
    time.sleep(0.4)  # let the agent apply it before trusting a flag that already matches
    while time.time() < deadline:
        with LOCK:
            sens = STATE.get("last_sensor") or {}
        if sens and bool(sens.get("ssh_on")) == on:
            return I_say("ssh_on" if on else "ssh_off", lang)
        time.sleep(0.1)
    print("ssh voice: robot did not confirm", flush=True)
    return I_say("ssh_fail", lang)


def I_say(key: str, lang: str) -> str:
    return intents_mod.say(key, lang)


def run_intent(intent) -> str:
    """Execute one stock command. Returns the spoken reply ("" = stock had none);
    face clip / action go to TURN_OUT. Robot motion is only ever a named action
    the agent's runner executes under its veto."""
    I = intents_mod
    n, lang = intent.name, _lang(intent)
    show, action, reply = "", "", ""
    if n in I.SIMPLE:
        show, action, key = I.SIMPLE[n]
        reply = I.say(key, lang) if key else ""
    elif n in I.DRIVE:
        act, key = I.DRIVE[n]
        if _on_charger() is not False:  # unknown counts as on the charger
            reply = I.say("on_charger", lang)
        else:
            action, reply = act, (I.say(key, lang) if key else "")
    elif n == "intent_system_leavecharger":
        if _on_charger() is False:
            reply = I.say("not_on_charger", lang)
        else:
            action, reply = "leave_charger", I.say("leave", lang)
    elif n == "intent_explore_start":
        with LOCK:
            sens = STATE.get("last_sensor") or {}
        if _on_charger() is not False:
            reply = I.say("on_charger", lang)
        elif not sens.get("explore_enabled"):
            reply = I.say("explore_off", lang)
        else:
            action, reply = "explore", I.say("explore", lang)
    elif n == "intent_system_ssh_enable":
        reply = _ssh_set(True, lang)
    elif n == "intent_system_ssh_disable":
        if intent.arg == "confirmed" or ssh_disable_ok(intent.text or "", getattr(intent, "raw", "")):
            reply = _ssh_set(False, lang)
        else:
            SSH_CONFIRM.update(until=time.time() + SSH_CONFIRM_S, lang=lang)
            reply = I.say("ssh_confirm", lang)
            print(f"ssh off needs a yes (router conf={router.LAST.get('conf')} how={router.LAST.get('how')})", flush=True)
    elif n == "intent_system_ssh_status":
        with LOCK:
            sens = STATE.get("last_sensor")
        queue_cmd(CMD_ACTION, b"ssh_status")  # robot shows SSH ON / SSH OFF for 1.5 s
        reply = I.say("ssh_unknown", lang) if not sens else I.say("ssh_on" if sens.get("ssh_on") else "ssh_off", lang)
    elif n == "intent_explore_stop":
        action, reply = "explore_stop", I.say("explore_stop", lang)
    elif n == "intent_imperative_shutup":
        action, show = "stop", "shutup"
    elif n == "intent_system_sleep":
        show = "sleep|"
    elif n == "intent_clock_time":
        tz, _ = voice_mod.hub_tz()
        import datetime as _dt
        now = _dt.datetime.now(tz)
        t = now.strftime("%-I:%M %p") if lang == "en" else now.strftime("%H:%M")
        reply = I.say("time", lang, t=t)
    elif n == "intent_clock_settimer_extend":
        sec = I.parse_duration(intent.arg)
        if sec <= 0 or sec > 24 * 3600:
            reply = I.say("timer_how_long", lang)
        else:
            TIMER.start(sec, lang, _timer_fire)
            reply = I.say("timer_set", lang, d=I.fmt_duration(sec, lang))
    elif n == "intent_clock_checktimer":
        left = TIMER.left()
        reply = I.say("timer_left", lang, d=I.fmt_duration(left, lang)) if left else I.say("no_timer", lang)
    elif n == "intent_global_stop_extend":
        reply = I.say("timer_cancel", lang) if TIMER.cancel() else I.say("no_timer", lang)
    elif n == "intent_character_age":
        bday = (os.environ.get("HUB_ROBOT_BIRTHDAY") or "").strip()
        try:
            import datetime as _dt
            days = (_dt.date.today() - _dt.date.fromisoformat(bday)).days
            reply = I.say("age", lang, d=I.fmt_age(days, lang)) if days >= 0 else I.say("age_unknown", lang)
        except ValueError:
            reply = I.say("age_unknown", lang)
        show = "howold"
    elif n in ("intent_imperative_volumeup", "intent_imperative_volumedown", "intent_imperative_volumelevel_extend"):
        if n.endswith("volumeup"):
            lvl = VOLUME.level + 1
        elif n.endswith("volumedown"):
            lvl = VOLUME.level - 1
        else:
            lvl = I.parse_level(intent.arg) or VOLUME.level
        lvl = VOLUME.set(lvl)
        reply = I.say("volume", lang, n=lvl)
        show = "volume_max" if lvl == 5 else ("volume_min" if lvl == 1 else "volume")
    elif n == "intent_photo_take_extend":
        img = FACE_ID.latest()
        if not img:
            reply = I.say("no_photo", lang)
        else:
            os.makedirs(PHOTO_DIR, exist_ok=True)
            path = os.path.join(PHOTO_DIR, time.strftime("photo-%Y%m%d-%H%M%S.jpg"))
            with open(path, "wb") as f:
                f.write(img)
            print(f"intent photo saved {path} bytes={len(img)}", flush=True)
            show, reply = "photo", I.say("photo", lang)
    elif n == "intent_names_username_extend":
        name = intent.arg.title() if intent.arg.isascii() else intent.arg
        out, code = enroll({"name": name, "count": 2, "gap": 1.0})  # explicit enroll: NVIDIA person check per frame
        reply = I.say("enrolled" if out.get("ok") else "enroll_fail", lang, n=out.get("name") or name)
    elif n in I.CUBE:
        reply, show = I.say("cube", lang), "cant_help"
    else:  # I.CANT and anything unhandled
        reply = I.say("cant", lang)
    if show:
        TURN_OUT["show"] = show.encode() if show.endswith("|") else f"anim|{show}".encode()
    TURN_OUT.update(action=action, intent=n)
    with LOCK:
        STATE["last_intent"] = {"intent": n, "lang": lang, "arg": intent.arg, "action": action,
                                "face": show, "reply": reply, "t": time.time()}
    print(f"intent {n} lang={lang} arg={intent.arg!r} action={action or '-'} face={show or '-'} reply={reply!r}", flush=True)
    return reply


def voice_loop() -> None:
    """One OpenAI turn at a time. Mic stays closed until the reply is queued."""
    while True:
        item = UTTERANCES.get()
        try:
            if isinstance(item, tuple):  # ("asleep", pcm)
                asleep_turn(item[1])
            else:
                run_turn(item)
        except Exception as exc:  # noqa: BLE001 - one bad turn must never kill the voice thread
            print(f"voice loop turn crashed {exc!r}", flush=True)
            try:
                with LOCK:
                    STATE["voice_busy"] = False
                    STATE["thinking"] = False
            except Exception:  # noqa: BLE001
                pass


def run_turn(pcm: bytes, text: str | None = None) -> None:
    """One reply turn; thinking is always cleared, even if a call raises."""
    with LOCK:
        running = bool(STATE.get("thinking"))
    if not running:  # turn was not opened by _on_audio (tests, /record)
        begin_think("queue")
    heard = text
    text, reply, audio, why = "", "", b"", "done"
    try:
        text, reply, audio = reply_turn(pcm, heard)
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
        show, action = TURN_OUT["show"], TURN_OUT["action"]
        if TURN_OUT["intent"]:
            why = "intent " + TURN_OUT["intent"]
        TURN_OUT.update(show=b"", action="", intent="")
        end_think(audio, why, show)
        if action:  # after idle/clip so the runner's own clip is not overwritten
            queue_cmd(CMD_ACTION, action.encode())
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
            queue_cmd(CMD_SPEAK, VOLUME.apply(pcm))
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
    """Keep the newest face frame on the hub (local only). It goes to NVIDIA
    only for an identity question or an explicit enroll."""
    FACE_ID.on_frame(jpeg)


TURN_PERSON = {"ctx": ""}  # set by reply_turn for an identity question, cleared after the chat call


def _person_context() -> str:
    return TURN_PERSON["ctx"] or NO_CONTEXT


voice_mod.PERSON_CONTEXT = _person_context
voice_mod.HISTORY = lambda: SESSION.messages()  # global: tests swap SESSION


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
            body["intents"] = {"enabled": INTENTS_ON, "volume": VOLUME.level, "timer_left_s": TIMER.left()}
            body["session"] = dict(SESSION.status(), asleep=dict(ASLEEP), chime=WAKE_CHIME, button=dict(BUTTON))
            body["openai_calls"] = dict(Api.CALLS)
            self._json(body)
            return
        if path == "/intents":
            self._json({"enabled": INTENTS_ON, "intents": intents_mod.status_table()})
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
        if path == "/action":
            # supervised tests only; the agent runs it under its veto (cliff/pickup/charger)
            name = str(data.get("name") or "")
            if name not in ("forward_test", "stop"):
                self._json({"ok": False, "error": "allowed: forward_test, stop"}, 400)
                return
            queue_cmd(CMD_ACTION, name.encode())
            print(f"http action {name}", flush=True)
            self._json({"ok": True, "action": name})
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
            res = FACE_ID.recognize(img, [])  # explicit enroll only: "is a face visible?"
            FACE_ID.enroll_calls += 1
            checks.append({"person": res["person"], "face": res.get("face_visible", res["person"]),
                           "ms": res["ms"], "error": res["error"][:80]})
            if res["error"] or not res["person"] or not res.get("face_visible", res["person"]):
                skipped += 1
                continue
        stored.append(FACE_DB.enroll(name, img)["id"])
    ok = bool(stored)
    out = {"ok": ok, "name": name, "stored": len(stored), "ids": stored, "skipped": skipped, "checks": checks}
    if not ok:
        out["error"] = "no person visible in the frames; stand 0.5-1 m in front of the robot, face it, and retry"
    print(f"face enroll name={name!r} stored={len(stored)} skipped={skipped}", flush=True)
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
<p>Live robot camera (1 fps, local preview only). Stand 0.5-1 m in front of Vector, face it, good light.
Photos go to the face-matching AI only when you click enroll or ask Vector "what's my name?".</p>
<img class="live" id="live" alt="no camera frame yet">
<form onsubmit="enroll(event)" class="row"><input name="name" placeholder="your name" required>
<button>enroll (3 photos, ~5 s)</button></form>
<div class="row" id="sess" style="font-weight:bold"></div>
<div class="row" id="who"></div>
<pre id="out"></pre><div id="list"></div>
<script>
function tick(){document.getElementById('live').src='/frame?kind=face&t='+Date.now();
 fetch('/status').then(r=>r.json()).then(j=>{const p=j.person||{};const s=j.session||{};
  document.getElementById('sess').textContent='voice session: '+(s.state||'?').toUpperCase()+' for '+s.for_s+'s'+
   ' (wakes '+s.wakes+', turns '+s.turns+', history '+s.history_turns+(s.idle_timeout_s?', idle timeout '+s.idle_timeout_s+'s':'')+
   ') | asleep: heard '+(s.asleep||{}).heard+', ignored '+(s.asleep||{}).ignored+', last '+JSON.stringify((s.asleep||{}).last||'');
  document.getElementById('who').textContent='last identity check: '+(p.present_name||'nobody')+
   ' conf '+p.confidence+' age '+p.age_s+'s person='+p.present_person+' checks '+p.calls+
   (p.skip?' ('+p.skip+')':'')+(p.error?' error: '+p.error:'');});}
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
    if SESSION.enabled:
        print(f"wake word on: asleep until 'Hey Vector', awake until 'Stop Vector'; "
              f"idle timeout {SESSION.idle_s or 'off'}", flush=True)
    else:
        print("wake word off (HUB_WAKE=0): always listening", flush=True)
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
