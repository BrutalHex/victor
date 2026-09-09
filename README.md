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

The robot reaches the development machine as `robot.mohammad.abbasi.com`, resolved by `/etc/hosts` on the robot to the current LAN IP.

## Quick start (once code exists)

```bash
cp .env.example .env
# fill OPENAI_API_KEY, OPENAI_MODEL, ROBOT_SSH_IP
docker compose -f hub/docker-compose.yml up -d --build
./deploy/push-hub-ip.sh
./deploy/sync-agent.sh
```

SSH to the robot must stay available on port 22 after every boot.
