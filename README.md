# Victor

Owner-built stack for a Vector 2.0 robot plus a Docker brain named `hub`.

The robot stays a thin real-time agent. The old laptop runs a tiny NVIDIA-exported ONNX edge model (CPU first) plus exploration, faces, and ChatGPT voice. IR cliffs on the robot remain the hard stop.

**Read [GROK_INSTRUCTIONS.md](GROK_INSTRUCTIONS.md) before writing code.** Leftover work is in [REMAINING.md](REMAINING.md).

## Layout

| Path | What |
|---|---|
| `GROK_INSTRUCTIONS.md` | Architecture, protocol, Yocto rules, phases, definition of done |
| `hub/` | Docker container `hub` (explore, faces, voice, ONNX edge) |
| `robot/` | On-robot agent (veto, spine, camera, mics, LCD) |
| `yocto/` | Our image recipes + A/B OTA packer |
| `deploy/` | SSH / first-flash / prove / `make-ota.sh` |

## Hub hostname

The robot reaches the development machine as `robot.mohammadabbasi.com`, resolved by `/etc/hosts` on the robot to the current LAN IP.

## First flash and SSH

BLE is used **once**: pair on the charger, push Wi-Fi, start the first OTA (`deploy/ble-bootstrap`). After SSH answers, BLE is disabled on the running image.

Later debug access is **by voice** (changed at the owner's request on 10 Oct 2026; no face check, it does not matter whether Vector sees you):

| | English | German | Persian |
|---|---|---|---|
| on | "Vector, enable SSH" / "turn on SSH" / "SSH on" | "SSH an" / "schalte SSH ein" / "aktiviere SSH" | "اس اس اچ رو روشن کن" |
| off | "Vector, disable SSH" / "turn off SSH" / "SSH off" | "SSH aus" / "mach SSH aus" / "deaktiviere SSH" | "اس اس اچ رو خاموش کن" |
| status | "Is SSH on?" / "SSH status" | "Ist SSH an?" | "وضعیت اس اس اچ" |

Vector answers "SSH is on" / "SSH is off" (in your language) once the robot confirms it, and the face shows `SSH ON` / `SSH OFF` for 1.5 s. Turning SSH **off** needs one of the exact phrases, or the OpenAI router at >= 0.85 with "SSH" and an off verb in the sentence; anything less and Vector asks "Did you want me to turn SSH off?" and only a "yes" within ~10 s does it, so a misheard sentence can't lock you out. Hub telemetry stays up while SSH is closed. The agent sets `/data/victor/ssh.enabled` and starts/stops `sshd.socket` (or `dropbear`); the state persists across reboots.

The old CHARGE-LATCH button gesture is off; `touch /data/victor/charge-latch.enabled` re-arms it. The button is now read from the right field (`touchLevel[1]`, offset 94, as in the vic HAL; the old read at 91 looked pressed on every frame), and with the gesture off a press only wakes the voice session (below): it never reaches the latch and never toggles SSH. Safety net unchanged: SSH off >= 24 h AND hub heartbeat missing 10 min AND on the charger -> SSH turns itself on (face `SSH AUTO`).

## Edge model

Hub runs a tiny NVIDIA TAO export (DetectNet_v2 ResNet10 or MobileNet-V2, ONNX INT8, \u226415 MB). Default backend is ONNX Runtime CPU so an old laptop without a useful GPU still works. TensorRT is optional. The model only votes `stop` / `back_off`. IR cliffs on the robot still win.

## Quick start

```bash
cp .env.example .env
# fill OPENAI_API_KEY, WIFI_*, VECTOR_BLE_PIN; ROBOT_SSH_IP defaults to 192.168.0.6
make test
make agent-arm
docker compose -f hub/docker-compose.yml up -d --build
./deploy/sync-agent.sh
./deploy/prove-phase0.sh
./deploy/prove-phase1.sh   # stops Anki, takes the spine; restore with ./deploy/restore-anki.sh
./deploy/prove-phase2.sh   # cliff cal + veto; wheels stay 0 unless explore.enabled
./deploy/prove-phase3.sh   # camera/audio, /faces, thinking bar
./deploy/prove-phase4.sh   # Yocto recipes + HTTP .ota packer
```

SSH (unlocked Vector / WireOS / our image):

```bash
./deploy/ssh.sh
# equivalent:
ssh -i keys/ssh_root_key \
  -o PubkeyAcceptedAlgorithms=+ssh-rsa \
  -o HostKeyAlgorithms=+ssh-rsa \
  root@$ROBOT_SSH_IP
```

Build the OTA and flash it (BLE, once, robot on charger in recovery) — full steps in [deploy/OTA.md](deploy/OTA.md):

```bash
make ota-deps      # once
make release       # build victor.ota + rollback.ota from the robot, flash via recovery, verify
make help          # all targets
```

First boot leaves SSH on so `ble-bootstrap` can finish. After that, voice owns port 22 (see "First flash and SSH").

Operator HTTP: `http://robot.mohammadabbasi.com:8080/status`, `/faces`, `/ui`. OpenAI key stays in `.env` on the hub.

## Face page (teach Vector who you are)

### By voice (no web page needed)

While Vector is awake ("Hey Vector"), stand 0.5–1 m in front of him, face him, with light on your face, and say
"my name is Mohammad" / "I'm Mohammad" / "call me Mohammad", "ich heiße Mohammad" / "ich bin Mohammad" /
"mein Name ist Mohammad", "اسم من محمد است" / "من محمد هستم". The OpenAI router decides whether it really is an
introduction and pulls out the name ("I am hungry", "ich bin müde", "من خسته هستم" enroll nobody; a name that was
not said is rejected). Vector looks up, takes about 3 camera frames over ~3–5 s, keeps only frames with exactly one
clear face (small background faces are ignored), stores them in the same face DB the web page uses, and says
"Nice to meet you, Mohammad!" (happy eyes). No face: "I can't see your face. Please look at me." and a ~6 s retry;
still nothing: nothing is stored and he says so. A name that already exists gets the new frames as extra references
("I've added a few more pictures"). Persian names are stored in Latin letters (محمد -> Mohammad) so one person
has one entry. (Optional later: "forget me" / "forget X"; today use the delete button on the page.)

"Who am I?" / "What's my name?" / "Wer bin ich?" / "Wie heiße ich?" / "من کی هستم؟" / "اسم من چیه؟" is answered from
the local match; NVIDIA only if the local match is uncertain; OpenAI phrases the reply as before.

Spontaneous hello: when a known face is in view (2 scans in a row), Vector says "Hi Mohammad!" with the happy
`hello` eyes, at most once per person per 2 h (`HUB_GREET_COOLDOWN_S`), never while speaking/thinking or within 20 s
of a voice turn, after a random 0.8–2.5 s pause and a re-check that you are still there. Asleep he only looks up
with happy eyes, no speech (`HUB_GREET_ASLEEP=look|speak|off`). Unknown faces: nothing (`HUB_GREET_UNKNOWN=1` makes
him ask "I don't think we've met. What's your name?" once per 6 h, awake only). Right after you enroll, the 2 h
cooldown starts, so he does not greet you again at once.

### How it decides (local first, NVIDIA budgeted)

- The robot already streams the face camera to the hub (640x480, 1 fps). The hub scans the newest frame locally
  (`hub/app/face_local.py`): ~1 scan/s while a face is around, ~0.5/s otherwise. YuNet finds faces; SFace makes a
  128-d embedding of a clear frontal face; cosine similarity against every stored reference photo.
  ≥ `HUB_FACE_LOCAL_SURE` (0.50) = that person, < `HUB_FACE_LOCAL_UNSURE` (0.32) = nobody enrolled; no call either way.
  Measured on the stored frames: owner across sessions 0.51–0.72, 63 strangers ≤ 0.31.
- Uncertain (between the two) -> one NVIDIA call (`hub/app/face_watch.py`): always for "who am I?", in the background
  only if it would lead to a greeting (not greeted for 2 h, nothing else going on), at most
  `HUB_FACE_BG_MAX_PER_HOUR` (6), and never once only `HUB_FACE_BG_RESERVE` (50) calls are left.
  If NVIDIA confirms (≥ 0.85) that embedding is kept as an extra local reference, so that view is local next time.
- Every answer is cached per face embedding for `HUB_FACE_CACHE_S` (10 min): the same person in view does not
  trigger repeat calls. No face in view = never a call.
- Hard budget for every NVIDIA request (retries included), shared by all callers (`hub/app/nv_budget.py`):
  `NVIDIA_RPM` (20/min), `NVIDIA_MAX_CALLS` (400) per `NVIDIA_CAP_WINDOW` (`day` = resets at local midnight,
  `total` = never). Persisted in `hub/data/nvidia_budget.json` across hub restarts; shown on `/status`
  (`nvidia_budget`) and on the Face page; every call is logged (`nvidia call reason=...`). Cap reached: local-only
  matching, nothing is said about it.
- Expected use: 0 calls while nobody or a confidently known face is in view; ~0–3 calls/hour in normal use
  (an uncertain view of a person not yet greeted, or an uncertain "who am I?"). Worst case in the background is
  6/hour. Enrollment never calls NVIDIA.
- Privacy: frames stay on the hub. Only a budgeted NVIDIA check sends one frame + up to 6 reference photos.
  OpenAI never receives camera images. No keys go on the robot. `HUB_FACE_ID=0` turns face features off;
  `HUB_GREET=0` only the greeting.

### Web page

Open **http://localhost:8080/ui** on the hub machine (or `http://robot.mohammadabbasi.com:8080/ui` from the LAN). It
shows a live preview from the robot's camera (about 1 fps), the enroll form, what the local scan sees, the NVIDIA
budget, and the list of enrolled people with their photos (voice enrollments appear here too).

1. Stand 0.5–1 m in front of Vector, facing him, with light on your face (not behind you).
2. Type your name, click **enroll**, and hold still about 5 s while it takes 3 photos. Frames without exactly one
   clear face are skipped (checked locally, no NVIDIA call).

```bash
curl -XPOST localhost:8080/faces -d '{"name":"Mohammad","count":3,"gap":1.5}'   # enroll
curl localhost:8080/faces                                                      # list
curl -XDELETE 'localhost:8080/faces?id=<id>'                                    # remove one photo
curl -XDELETE 'localhost:8080/faces?name=Mohammad'                              # remove a person
```

## Wake word: "Hey Vector" ... "Stop Vector"

Vector no longer answers everything it hears. After a hub start it is **asleep**; a conversation runs from
"Hey Vector" to "Stop Vector" (owner's request, 10 Oct 2026). No extra model or process: the robot streams the mic
as before, the hub noise gate and the normal speech-to-text run, and while asleep the transcript is only checked
for the wake phrase (`hub/app/wake.py`). Anything else is dropped silently: no router, chat, speech or thinking
face, so the only cost of background talk is one transcription call.

- Wake: "Hey Vector" / "Hi Vector" / "Okay Vector" / "Vector, ...", "Hallo Vektor", "هی وکتور" / "سلام وکتور"
  (Victor / Vektor / Viktor accepted), at the start of the sentence or after a greeting anywhere in it.
  Near misses right after a greeting also count ("Hey Vecta", "Evektor", "Эй, Вектор", "Hey Becca." alone);
  a sentence that only contains a Victor-like word ("Victor Hugo wrote...", "Hallo Viktoria") does not.
  Cue: eyes look up at you (`lookatme`), a soft two-note chime and a short "Yes?" / "Ja?" / "بله؟"
  (`HUB_WAKE_CHIME=0` mutes the chime). "Hey Vector, what time is it?" answers at once from the same transcript.
  Saying "Hey Vector" again while awake gives the same cue + "Yes?" (never silence).
- **Eyes show the state**: awake = normal open eyes (listening); asleep = half-lidded, dimmer eyes looking slightly
  down. The hub sends `session|awake` / `session|asleep` on every change and every 10 s.
- Asleep the noise gate is looser (a quick, quiet "Hey Vector" from across the room is short and has little steady
  voicing): `HUB_ASLEEP_MIN_SNR` (3.5), `HUB_ASLEEP_MIN_VOICED` (4), `HUB_ASLEEP_MIN_FRAMES` (8). Asleep clips are
  levelled up before STT (`HUB_ASLEEP_LEVEL=0` turns that off). `HUB_ASLEEP_PROMPT=1` adds "they say Hey Vector" to
  the STT prompt; it is off because in a replay it made STT write "Hey Vector" for plain room noise. Hub log lines `asleep WAKE|ignore|empty` show the
  transcript and the gate numbers.
- A garbled 1-2 word transcript while awake ("Mof Berlin.") gets "Sorry?" instead of a chat answer.
- **Backpack button**: one press while asleep wakes him the same way (same cue). A press while awake gives the
  look-up + chime.
  The agent debounces the button (40 ms; a level stuck "pressed" gives one press, never a stream), logs
  `button press n=N`, writes `/data/victor/button.txt`, and sends the running count in SENSOR; the hub logs
  `button_press -> wake` and shows `session.button` in `/status`. A press never touches SSH (the latch is off).
- Awake: every utterance runs the normal pipeline (commands, chat with web search, "what's my name?" face check,
  SSH by voice with its rules); the session's turns are kept as chat history and cleared when it ends.
- Sleep: "Stop Vector" / "Vector, stop" / "Stop listening", "Vektor stopp" / "Hör auf zuzuhören", "وکتور بسه" /
  "وکتور استاپ" (whole sentence only). It also stops any motion or wander, says "Okay, I'll stop listening."
  in your language and plays the `goodnight` face. Plain "stop" (or "Vector, stop driving") stays the motion stop.
- No idle timeout (owner's decision): he goes to sleep only on "Stop Vector" (`HUB_SESSION_IDLE_S` stays 0). `HUB_SESSION_HISTORY` (default 8) = turns kept as context.
- `/status` -> `session` (state, wakes, turns, history, asleep heard/ignored, button presses, `openai_calls` per
  endpoint); `/ui` shows the state on top.
- The name: system prompt, router and STT prompt say "Your name is Vector. You are a Vector robot"; STT's
  "Victor/Vektor/Viktor" counts as addressing Vector, and a reply never introduces itself as Victor.
- Undo: `HUB_WAKE=0` in `.env` + `make hub` = always listening, exactly as before.

## Voice commands (stock Vector set)

The hub matches the transcript against the stock Vector / wire-pod command set in English, German and
Persian (`hub/app/intents.py`, whole-utterance patterns, no LLM). A match runs directly, without a chat
call; anything else goes to normal chat. `GET /intents` lists every command and its status; `/status`
shows the last one under `last_intent`. Set `HUB_INTENTS=0` in `.env` to switch matching off.

- Works: what time is it, set / check / cancel a timer, volume up / down / 1-5 (hub speech gain), take a
  picture (saved on the hub in `/app/data/photos`), my name is ... (enrolls your face), hello / good
  morning / good night / goodbye, I love you, good robot, bad robot, sorry, shut up / stop, go to sleep,
  wake up, how old are you (set `HUB_ROBOT_BIRTHDAY`).
- Robot actions (run by the agent under the on-robot veto; face clips from DDL animations): look at me,
  fist bump, dance / do a trick, come here, go forward, back up, turn left / right / around,
  get off the charger. On the charger only "get off the charger" drives; the lift never moves there.
  `touch /data/victor/voice-drive.disabled` on the robot stops every voice-driven wheel move.
- Not yet (Vector says so politely): go to your charger (needs charger vision), cube games
  (cube needs BLE), blackjack, eye colour, messages, Alexa.
- Explore / go explore / stop exploring: see "Autonomous wander" below.
- Weather and knowledge questions stay with chat (web search). "What's my name?" keeps the face check.

## Back touch (petting)

Stroke Vector's back: after ~0.4 s the eyes go to happy squints, growing to the "^ ^" bliss face the
longer you pet, with a soft purr when the speaker is free. Letting go plays the "get out" face. The
agent writes the live sensor to `/data/victor/touch.txt`. Touch never toggles SSH.

## Idle life

While nothing else is going on (no turn, action, petting or veto), the agent glances its eyes around every
5-12 s, sometimes nudges its head up or down, and glances (eyes + head up) toward a sudden sound. Head only:
no wheels, no lift (the lift was part of the old SSH latch gesture). `touch /data/victor/idle-life.disabled`
on the robot turns it off. Idle life pauses while a wander session runs.

## Autonomous wander

Runs on the robot (`robot/agent/internal/wander`), never from the hub. It drives only when BOTH the flag file
`/data/victor/explore.enabled` exists on the robot (default: absent) AND someone says "explore" / "go explore"
(German "erkunde", "fahr herum"; Persian "برو بگرد") while Vector is off the charger.

- ~120 mm/s (VECTOR_EXPLORE_MMPS, capped at 140; slows from ~30 cm before an obstacle and for the end of a leg) legs of 15-40 cm, then a 2-4 s pause with a short head look-around; ends after 10 minutes.
- Obstacle (ToF) closer than 100 mm ahead: stop and turn away 90-150 degrees (turns at VECTOR_TURN_DPS, default 180 deg/s). Wheels stalled 1.5 s: same.
- Cliff sensors are checked every 20 ms control tick. Front cliff: brake in reverse on that same tick (front sensor
  overrun <= ~25 mm even at the 140 mm/s cap, unit-tested with a braking model); the only motion then allowed is a
  60 mm/s reverse of ~5 cm (stopping distance + 3 cm) while both rear cliff sensors see floor, then a turn. Cliffs front and rear: halt until someone restarts it.
- Picked up, falling, low battery, put on the charger, hub gone > 1 s: the session ends.
- Refuses to start on the charger, without cliff calibration (`/data/victor/cliffs.cal`), or at an edge.
- Stop by voice ("stop", "shut up", "stop exploring"), by picking him up, or `rm /data/victor/explore.enabled`.
- Never moves the lift. Status: `/data/victor/wander.txt`, agent log lines `wander ...`.
