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

Later debug access is **CHARGE-LATCH** (robot on charger: double-click button, raise and lower the lift, triple-click). That toggles port 22. Face shows `SSH ON` / `SSH OFF`. Hub telemetry stays up while SSH is closed.

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

First boot leaves SSH on so `ble-bootstrap` can finish. After that, CHARGE-LATCH owns port 22.

Operator HTTP: `http://robot.mohammadabbasi.com:8080/status`, `/faces`, `/ui`. OpenAI key stays in `.env` on the hub.

## Face page (teach Vector who you are)

Open **http://localhost:8080/ui** on the hub machine (or `http://robot.mohammadabbasi.com:8080/ui` from the LAN). It shows a live preview from the robot's camera (about 1 fps), the enroll form, and the list of enrolled people with their photos.

To enroll:

1. Stand 0.5–1 m in front of Vector, facing him, with light on your face (not behind you). Check the preview until your face is clear, not a silhouette.
2. Type your name, click **enroll**, and hold still about 5 s while it takes 3 photos. If it reports skipped frames, adjust the light or position and retry.
3. Wait about 10 s, then ask "What's my name?" / "Wie heiße ich?" / "اسم من چیه؟".

Same thing from a shell:

```bash
curl -XPOST localhost:8080/faces -d '{"name":"Mohammad","count":3,"gap":1.5}'   # enroll
curl localhost:8080/faces                                                      # list
curl -XDELETE 'localhost:8080/faces?id=<id>'                                    # remove
```

Matching runs on the hub through NVIDIA (`HUB_FACE_MODEL`, key in `NVIDIA_API_KEY` in `.env`); the result is in `/status` under `person`. Vector only says a name when it recognised an enrolled face with confidence ≥ `HUB_FACE_MIN_CONF` (0.7); otherwise it says it doesn't recognise you. No keys go on the robot. Set `HUB_FACE_ID=0` and run `make hub` to turn it off.
