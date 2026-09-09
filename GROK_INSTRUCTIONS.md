# GROK_INSTRUCTIONS.md

Master build spec for coding agents working in `https://github.com/BrutalHex/victor`.

This file is the source of truth. Implement against it. Do not invent a second architecture. Do not install a third-party community firmware (WireOS, Viccyware, etc.) as the product. Community trees may be **read as hardware reference only**.

Owner: Mohammad Abbasi (`BrutalHex`). Domain used by the robot to reach the development machine: `robot.mohammadabbasi.com`.

---

## 0. What you are building

A two-computer system:

| Node | Hardware | Role |
|---|---|---|
| **Robot** | Anki / Digital Dream Labs Vector 2.0, Qualcomm APQ8009, 512 MB RAM, 4 GB eMMC | Thin real-time agent. Speaks to motors and sensors. Streams camera, mics, spine frames. Renders face UI. Keeps SSH open. |
| **Hub** | Development machine, Docker container named `hub`, same Wi-Fi as the robot | Brain. Fall prevention, free exploration, face labels, speech via OpenAI, all high-level behavior. |

The robot must never depend on Digital Dream Labs cloud. All personality and intelligence live in `hub`.

### Non-goals

- Do not flash Ubuntu, Debian, Fedora, or NVIDIA JetPack onto the APQ8009.
- Do not replace Qualcomm SBL / ABOOT with a generic bootloader.
- Do not delete the recovery slot. A failed image must still boot recovery so SSH / OTA can be retried.
- Do not put the OpenAI API key on the robot.
- Do not let an LLM command wheel PWM directly.
- Do not disable cliff sensors “until the model works”.

---

## 1. Hardware facts the agent must treat as fixed

Vector 2.0 is not a Raspberry Pi.

- SoC: Qualcomm APQ8009 (Snapdragon 212 class), 4× Cortex-A7, default ~533 MHz, theoretical max ~1.3 GHz (thermal risk).
- RAM: 512 MB. Storage: 4 GB eMMC.
- Userspace ABI: `armel` / soft-float because of Qualcomm blobs. Cross-compile with `-mfloat-abi=softfp -march=armv7-a -mfpu=neon-vfpv4`.
- Kernel lineage: Qualcomm `msm-3.18` BSP. Camera, WLAN, and charging need vendor modules + `/firmware` blobs.
- Display: 184×96 RGB565 face LCD (SDK face blit is 35328 bytes = 184×96×2).
- Camera: ~1280×720 Bayer through `mm-anki-camera`. SDK preview is often 640×360 JPEG.
- Body board (syscon): separate MCU. Motors, 4 cliff IR, ToF, battery, backpack LEDs, touch, button, mic PCM travel on the **spine** UART, typically `/dev/ttyHS0`.
- Spine frame fields the agent must parse:

```
seq                  u32
cliffs[4]            u32   // front-left, front-right, rear-left, rear-right
encoders             4 × {pos i32, dlt i32, tm u32}  // right wheel, left wheel, lift, head
motor order          right wheel, left wheel, lift, head
batt_voltage         i16
charger_voltage      i16
body_temp            i16
touch                u16
button               bool
mic_pcm              i16[]
prox_sigma_mm        u8
prox_raw_range_mm    u16
```

- Four beamforming mics on the backpack. PCM arrives on the spine frames.
- IMU is on the head SoC. Include it in the sensor datagram when the node exists.
- Wi-Fi: 2.4 GHz only.

Yocto is the **image factory**, not the product. The product is: vendor-compatible kernel + **our** rootfs + **our** `victor-agent` + Docker `hub`.

---

## 2. Safety contract (non-negotiable)

Fall prevention is a hard real-time problem. Wi-Fi plus an LLM is not.

```
spine / IMU  --50-100 Hz-->  victor-agent safety loop  --veto-->  motors
                                      |
                                      | binary sensor stream
                                      v
                                    hub safety + ML
                                      |
                                      | CommandFrame (skills, not raw PWM)
                                      v
                                victor-agent executor
```

### On-robot veto (must run even if hub is dead)

Trigger any of these → wheels = 0, optional short reverse, then hold:

1. Any front cliff value crosses the calibrated “no floor” threshold.
2. ToF reports unusable range while a forward command is active.
3. IMU / pickup / free-fall flag.
4. Hub heartbeat missing for `HEARTBEAT_MS` (default 250 ms).
5. Command TTL expired.
6. Battery below `BATT_STOP_MV`.

The robot may wander only while the veto is clear.

### On-hub analysis

Hub runs a second loop on streamed cliffs, ToF, IMU, and camera:

- Classical: threshold + short-window derivative on cliffs, tilt estimate from IMU.
- Optional small model: MobileNet-class or tiny edge detector on 160×90 (or 320×180) frames answering `{safe, edge, obstacle, unknown}`.
- Hub may only emit skills: `stop`, `back_off`, `turn_left`, `turn_right`, `creep_forward`, `look_up`, `look_down`, `dock`, `idle`.
- If classical veto and model disagree, classical wins.

Never stream motor setpoints from ChatGPT.

---

## 3. Network and naming

Both machines are on the same Wi-Fi.

| Name | Meaning |
|---|---|
| `robot.mohammadabbasi.com` | Hostname the **robot** uses for the hub. Not public DNS. Resolved by `/etc/hosts` on the robot to the current LAN IPv4 of the development machine. |
| Hub container publish | Host network or published ports on the development machine. |

### Robot `/etc/hosts`

```
# managed-by: victor-agent
<DEV_MACHINE_LAN_IP>    robot.mohammadabbasi.com hub
```

Requirements:

- File must stay writable. Yocto image must **not** freeze `/etc/hosts` read-only. Prefer a rw overlay or a generated fragment under `/etc/hosts.d` included by a systemd oneshot.
- Ship `/usr/bin/victor-set-hub-ip` that rewrites the managed block and does not touch other lines.
- Agent retries: if TCP to `robot.mohammadabbasi.com:7443` fails, use `/data/victor/hub.env` fallback IP.
- After DHCP change on the development machine, operator runs `scripts/push-hub-ip.sh` over SSH.

### Ports (development machine / hub)

| Port | Proto | Use |
|---|---|---|
| 22 | TCP | SSH on the **robot** (always on). |
| 7443 | TCP | gRPC control + reliable telemetry (`victor.hub.v1`) |
| 7500 | UDP | Camera frames (binary) |
| 7501 | UDP | Microphone PCM (binary) |
| 7502 | UDP | Spine sensor datagrams (binary) |
| 8080 | TCP | Hub operator HTTP (status, faces, logs). |

Robot listens on **22 only** for administration. All other sockets are outbound to the hub.

---

## 4. Binary protocol

Do not use JSON on the hot path. Do not use HTTP for sensors.

### 4.1 Framing (UDP datagrams)

```
magic[4] = "VCT1"
msg_type u8
flags    u8
seq      u32 LE
t_ns     u64 LE
len      u32 LE
payload  [len]
crc32    u32 LE
```

`msg_type`: `1 SENSOR`, `2 AUDIO`, `3 VIDEO`, `4 UI_ACK`.

Target rates:

- SENSOR: 50 Hz
- AUDIO: 16 kHz PCM16, 20 ms packets, mono mix on robot by default
- VIDEO: 10–15 fps JPEG 320×180 for navigation; 5 fps 640×360 for faces

If Wi-Fi cannot hold 15 fps, drop video first, never sensors.

### 4.2 SENSOR payload (packed LE, keep under 64 bytes)

```
cliffs[4]           u16
prox_mm             u16
prox_quality        u8
imu_ax ay az        i16
imu_gx gy gz        i16
enc_rw enc_lw       i32
enc_lift enc_head   i32
batt_mv             u16
charger_mv          u16
touch               u16
flags               u16   // button, picked_up, on_charger, falling
```

### 4.3 gRPC `victor.hub.v1`

See `proto/victor_hub.proto` (to be generated from this spec).

Services: `Heartbeat`, `CommandStream`, `ReportFace`, `SetDisplay`.

`HubCommand` bodies: `Stop`, `Twist` (vx, wz), `PoseDelta`, `FaceUi` (thinking bar), `Speak` (PCM from hub), `Led`.

`Twist` is the most the hub may ask of locomotion. Robot scales it and still applies veto.

Heartbeat: 50 ms each way. Miss 5 → robot veto.

---

## 5. Repository layout

```
.
├── GROK_INSTRUCTIONS.md
├── README.md
├── proto/victor_hub.proto
├── robot/agent  robot/ui  robot/scripts  robot/systemd
├── hub/Dockerfile  hub/docker-compose.yml  hub/app  hub/models
├── yocto/meta-victor
├── deploy/
└── keys/          # never commit private keys
```

- Robot agent: C++17 or Rust. Python on the APQ8009 is bring-up only, not the shipped safety loop.
- Hub: Python 3.12 + grpcio + OpenCV + onnxruntime.
- Secrets stay on the hub. Never write `OPENAI_API_KEY` to the robot.

---

## 6. Yocto image (`yocto/meta-victor`)

### Must contain

- Vendor kernel + modules + firmware blobs for APQ8009, WLAN, camera, spine UART.
- Dropbear or OpenSSH **enabled at boot**, port 22, root login by key only.
- `victor-agent` at `/usr/bin/victor-agent`.
- systemd units `Restart=always` for agent and sshd.
- `/data` mounted `rw,exec` (stock often mounts `/data` noexec).
- Writable hosts mechanism (`/usr/bin/victor-set-hub-ip`).
- No `anki-robot.target` starting `vic-engine` / `vic-cloud` / `vic-gateway`.

```
IMAGE_FEATURES += "ssh-server-dropbear"
DISTRO_FEATURES:append = " systemd wifi"
VIRTUAL-RUNTIME_init_manager = "systemd"
```

### What “wipe / override the OS” means

You cannot safely `dd` Ubuntu over `system_a` and expect motors to work.

Correct override:

1. Unlock the production bootloader so the unit accepts a **dev** OTA. Keep the robot on the charger.
2. Keep SBL + ABOOT + recoveryfs.
3. Build our OTA (`boot` + `sysfs`) with our rootfs.
4. Install via recovery `ota-start http://<dev-ip>/<file>.ota` (recovery often cannot pull HTTPS).
5. Write the inactive A/B slot, mark bootable, reboot. Recovery remains if it fails.

If a full Yocto rebuild is not ready, Phase 1 is: stay on an unlocked vendor kernel, mask Anki units, push `victor-agent` over SSH.

### SSH must survive every boot

```
ssh -i keys/ssh_root_key \
  -o PubkeyAcceptedAlgorithms=+ssh-rsa \
  -o HostKeyAlgorithms=+ssh-rsa \
  root@<ROBOT_LAN_IP>
```

After every image build: boot to multi-user, confirm port 22 is listening, agent healthy.

Build Yocto inside Docker on the development machine (Docker is already installed).

---

## 7. Hub container

Container name: `hub`. `network_mode: host`. Env from `.env`.

Ports: 7443 gRPC, 7500 video UDP, 7501 audio UDP, 7502 sensor UDP, 8080 HTTP.

```
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_TRANSCRIBE_MODEL=gpt-4o-mini-transcribe
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_VOICE=alloy
HUB_PUBLIC_NAME=robot.mohammadabbasi.com
ROBOT_SSH_IP=
ROBOT_SSH_KEY=keys/ssh_root_key
```

---

## 8. Hub modules

- `safety.py` — 50 Hz cliffs/ToF/IMU, classical veto wins over ML.
- `explore.py` — desk wander state machine IDLE/CREEP/LOOK/TURN/BACK_OFF/DOCK. Max 40–60 mm/s until proven.
- `faces.py` — local embeddings in SQLite, operator names via `:8080/faces`. OpenAI is not the face DB.
- `voice.py` — VAD → thinking bar → transcribe → `OPENAI_MODEL` → TTS PCM back to robot.
- `display.py` — 184×96 modes: eyes, thinking bar, name caption, error codes.

One owner of the spine. Do not keep `vic-engine` running “for animations”.

---

## 9. Phases (do in order)

0. SSH access on charger + Wi-Fi. Document owner unlock. No CPU-swap scripts.
1. Mask Anki services. Stream SENSOR. Hub logs. Face shows E-OK. SSH still works.
2. Calibrate cliffs. On-robot veto. Explorer on blocks, then desk.
3. Camera, mics, faces, thinking bar, OpenAI voice.
4. Our Yocto OTA with SSH + agent baked in.

---

## 10. Definition of done

- [ ] `docker compose up` starts `hub`.
- [ ] Robot `/etc/hosts` maps `robot.mohammadabbasi.com` to the dev machine.
- [ ] Agent streams SENSOR ≥ 20 Hz plus audio/video at spec rates.
- [ ] Killing the hub container stops wheels within 250 ms.
- [ ] Desk-edge slide triggers on-robot veto without hub.
- [ ] Robot wanders the desk at low speed.
- [ ] Speech shows thinking bar, then spoken reply from `OPENAI_MODEL`.
- [ ] Named face is spoken and shown on the LCD.
- [ ] SSH works before agent start, after agent start, and after reboot.
- [ ] No OpenAI key on the robot filesystem.

---

## 11. Rules for the coding agent

1. Commit only to `https://github.com/BrutalHex/victor`.
2. Never commit `.env`, API keys, SSH private keys, or vendor firmware blobs.
3. Prefer small compiling slices per commit.
4. Unknown spine opcodes → stub + `TODO(hardware)`. Do not guess motor signs on a live desk.
5. Reimplement reference HALs in this repo. Do not vendor WireOS as the product.
6. Keep this file updated if protocol numbers change.
7. English for code and comments.
