# Victor

Owner-built stack for a Vector 2.0 robot plus a Docker brain named `hub`.

The robot stays a thin real-time agent. The development machine runs all intelligence (fall veto, desk exploration, face names, ChatGPT voice).

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

## Quick start (once code exists)

```bash
cp .env.example .env
# fill OPENAI_API_KEY, OPENAI_MODEL, ROBOT_SSH_IP
docker compose -f hub/docker-compose.yml up -d --build
./deploy/push-hub-ip.sh
./deploy/sync-agent.sh
```

First boot leaves SSH on so `ble-bootstrap` can finish. After that, CHARGE-LATCH owns port 22.
