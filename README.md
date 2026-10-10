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

Open **http://localhost:8080/ui** on the hub machine (or `http://robot.mohammadabbasi.com:8080/ui` from the LAN). It shows a live preview from the robot's camera (about 1 fps), the enroll form, and the list of enrolled people with their photos.

To enroll:

1. Stand 0.5–1 m in front of Vector, facing him, with light on your face (not behind you). Check the preview until your face is clear, not a silhouette.
2. Type your name, click **enroll**, and hold still about 5 s while it takes 3 photos. If it reports skipped frames, adjust the light or position and retry.
3. Ask "What's my name?" / "Wie heiße ich?" / "اسم من چیه؟" while facing Vector.

Same thing from a shell:

```bash
curl -XPOST localhost:8080/faces -d '{"name":"Mohammad","count":3,"gap":1.5}'   # enroll
curl localhost:8080/faces                                                      # list
curl -XDELETE 'localhost:8080/faces?id=<id>'                                    # remove
```

Matching runs on the hub through NVIDIA (`HUB_FACE_MODEL`, key in `NVIDIA_API_KEY` in `.env`), and only on demand: nothing is sent in the background. A camera frame leaves the hub only when (a) you click **enroll** (each photo is checked for a visible face) or (b) a voice turn is an identity question ("what's my name / who am I / do you know me", "wie heiße ich / wer bin ich / kennst du mich", "اسم من چیه / من کی هستم / منو میشناسی"). Then the hub takes the newest face frame (≤ `HUB_FACE_FRAME_MAX_AGE_S`, 5 s), makes one call (retries on a busy endpoint, `HUB_FACE_TIMEOUT` 25 s) while Vector shows the thinking face, and puts the result into that turn's prompt only. There are no periodic checks and no automatic greeting. OpenAI never receives camera images (STT gets audio, chat gets text). The camera preview on `/ui` stays on the hub. The last check is in `/status` under `person` (`calls` = identity checks, `enroll_calls` = enroll checks). Vector only says a name when it recognised an enrolled face with confidence ≥ `HUB_FACE_MIN_CONF` (0.7); otherwise it says it doesn't recognise you or can't see you. No keys go on the robot. Set `HUB_FACE_ID=0` and run `make hub` to turn it off.

## Wake word: "Hey Vector" ... "Stop Vector"

Vector no longer answers everything it hears. After a hub start it is **asleep**; a conversation runs from
"Hey Vector" to "Stop Vector" (owner's request, 10 Oct 2026). No extra model or process: the robot streams the mic
as before, the hub noise gate and the normal speech-to-text run, and while asleep the transcript is only checked
for the wake phrase (`hub/app/wake.py`). Anything else is dropped silently: no router, chat, speech or thinking
face, so the only cost of background talk is one transcription call.

- Wake: "Hey Vector" / "Hi Vector" / "Okay Vector" / "Vector, ...", "Hallo Vektor", "هی وکتور" / "سلام وکتور"
  (Victor / Vektor / Viktor accepted), at the start of the sentence or after a greeting anywhere in it.
  Cue: eyes look up at you (`lookatme`) plus a soft two-note chime (`HUB_WAKE_CHIME=0` mutes the chime).
  "Hey Vector, what time is it?" answers at once from the same transcript.
- **Backpack button**: one press while asleep wakes him the same way (same cue). A press while awake is only logged.
- Awake: every utterance runs the normal pipeline (commands, chat with web search, "what's my name?" face check,
  SSH by voice with its rules); the session's turns are kept as chat history and cleared when it ends.
- Sleep: "Stop Vector" / "Vector, stop" / "Stop listening", "Vektor stopp" / "Hör auf zuzuhören", "وکتور بسه" /
  "وکتور استاپ" (whole sentence only). It also stops any motion or wander, says "Okay, I'll stop listening."
  in your language and plays the `goodnight` face. Plain "stop" (or "Vector, stop driving") stays the motion stop.
- Optional idle timeout: `HUB_SESSION_IDLE_S=600` ends a session after 10 min without a turn (default 0 = only
  "Stop Vector"). `HUB_SESSION_HISTORY` (default 8) = turns kept as context.
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

- ~40 mm/s legs of 15-40 cm, then a 2-4 s pause with a short head look-around; ends after 10 minutes.
- Obstacle (ToF) closer than 100 mm ahead: stop and turn away 90-150 degrees. Wheels stalled 1.5 s: same.
- Front cliff: stop at once; the only motion then allowed is a slow reverse of 4 cm while both rear cliff sensors
  see floor, then a turn. Cliffs front and rear: halt until someone restarts it.
- Picked up, falling, low battery, put on the charger, hub gone > 1 s: the session ends.
- Refuses to start on the charger, without cliff calibration (`/data/victor/cliffs.cal`), or at an edge.
- Stop by voice ("stop", "shut up", "stop exploring"), by picking him up, or `rm /data/victor/explore.enabled`.
- Never moves the lift. Status: `/data/victor/wander.txt`, agent log lines `wander ...`.
