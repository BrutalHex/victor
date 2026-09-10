# Victor

Owner-built stack for a Vector 2.0 robot plus a Docker brain named `hub`.

The robot stays a thin real-time agent. The old laptop runs a tiny NVIDIA-exported ONNX edge model (CPU first) plus exploration, faces, and ChatGPT voice. IR cliffs on the robot remain the hard stop.

**Read [GROK_INSTRUCTIONS.md](GROK_INSTRUCTIONS.md) before writing code.** That file is the build spec for coding agents working in this repository.

## Layout

| Path | What |
|---|---|
| `GROK_INSTRUCTIONS.md` | Architecture, protocol, Yocto rules, phases, definition of done |
| `hub/` | Docker container `hub` (to be implemented) |
| `robot/` | On-robot agent (to be implemented) |
| `yocto/` | Our image recipes (to be implemented) |
| `deploy/` | SSH / OTA helpers (to be implemented) |

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

First flash (BLE, once, robot on charger in recovery): `./deploy/first-flash --pin … --ssid … --password … --url http://…/victor.ota`

First boot leaves SSH on so `ble-bootstrap` can finish. After that, CHARGE-LATCH owns port 22.
