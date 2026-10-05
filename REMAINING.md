# Remaining work

Source of truth: [GROK_INSTRUCTIONS.md](GROK_INSTRUCTIONS.md).

Do not ship WireOS as the product. Community trees are hardware reference only. OpenAI stays on the hub.

## Already true (Phase 0–4 code)

- SSH as `root@192.168.0.6` with `keys/ssh_root_key`
- `deploy/ble-bootstrap` + `deploy/first-flash` (code; not run — this host has no BLE and we have no vendor `.ota`)
- CHARGE-LATCH FSM unit-tested; `latch-simulate` toggles port 22
- Telemetry log keeps growing while SSH is off
- BLE masked (`ankibluetoothd` / `vic-switchboard` inactive, `/data/victor/ble.disabled`)
- No OpenAI key on the robot
- Hub container: UDP 7500/7501/7502, TCP 7443, HTTP 8080 (`/status`, `/faces`, `/think`, `/say`, `/ui`)
- Phase 1 spine owner, Phase 2 veto + explorer
- Phase 3: VCT1 AUDIO/VIDEO, face SQLite, thinking bar, STT→`OPENAI_MODEL`→TTS, ONNX edge vote `stop`/`back_off` (IR cliffs still win)
- Phase 4 recipes: running image vs recoveryfs, first-boot SSH ON + BLE mask, `/data` `rw,exec`, `deploy/make-ota.sh` A/B HTTP payload

```bash
make test
make prove3          # robot + reverse tunnel (this VM is NAT’d)
make prove4          # recipes + dummy .ota, no flash
docker compose -f hub/docker-compose.yml up -d --build
```

## Hardware leftovers (cannot finish on this NAT VM)

- First flash from `ble-bootstrap` with our HTTP `.ota` (needs BLE radio + vendor kernel images)
- Physical CHARGE-LATCH on the charger
- Hub hostname `robot.mohammadabbasi.com` on a same-LAN laptop (`./deploy/push-hub-ip.sh`)
- Drop a real NVIDIA TAO ONNX (≤15 MB) in `hub/models/` to replace `tiny_edge.onnx`
- Named-face speak + ChatGPT voice on a live camera with `OPENAI_API_KEY` on the hub only
- Bitbake `victor-image` with the vendor APQ8009 kernel and flash via recovery `ota-start`

## Definition of done

- [x] No OpenAI key on the robot
- [x] `docker compose up` starts `hub` (robot talks to it on the same LAN; this VM is NAT’d — prove-phase3 uses a reverse tunnel)
- [ ] `/etc/hosts` maps `robot.mohammadabbasi.com` to the laptop (`push-hub-ip.sh` / first boot)
- [x] SENSOR ≥ 20 Hz plus audio/video at spec rates (code; prove-phase3 checks ingest)
- [x] Killing hub stops wheels within 250 ms (Phase 2)
- [x] Desk-edge veto without hub (Phase 2)
- [x] Low-speed desk wander (`prove-explore.sh --drive`)
- [x] Speech with thinking bar + spoken reply (code; live OpenAI on hub)
- [x] Named face spoken and shown on the LCD (code; enroll via `:8080/faces`)
- [x] SSH works before agent, after agent, and after reboot (Phase 1)
