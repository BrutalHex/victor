# Remaining work

Source of truth: [GROK_INSTRUCTIONS.md](GROK_INSTRUCTIONS.md). This file is the leftover list after Phase 0 on Vector-W1V9 (`192.168.0.6`).

Do not ship WireOS as the product. Community trees are hardware reference only. OpenAI stays on the hub.

## Already true (Phase 0)

- SSH as `root@192.168.0.6` with `keys/ssh_root_key`
- `deploy/ble-bootstrap` + `deploy/first-flash` (code; not run — this host has no BLE and we have no `.ota`)
- CHARGE-LATCH FSM unit-tested; `latch-simulate` toggles port 22
- Telemetry log keeps growing while SSH is off
- BLE masked (`ankibluetoothd` / `vic-switchboard` inactive, `/data/victor/ble.disabled`)
- No OpenAI key on the robot
- Hub container exists (UDP 7502 / TCP 7443 / HTTP 8080)

## Phase 1 — done on Vector-W1V9

Mask Anki. One owner of the spine. Stream SENSOR. Face `E-OK`. SSH still works.

Proven 2026-09-10:

- `anki-robot.target` / `vic-engine` stopped; `sshd.socket` stayed up
- `/dev/ttyHS0` owned by `victor-agent`; motors held at 0
- Live SENSOR: `batt=3587 charger=4986 flags=5` (on charger)
- `/data/victor/spine.ok` and `face.rgb565` (`E-OK`)
- SSH before mask, after mask, after agent restart
- Restore stock face: `./deploy/restore-anki.sh`

Still soft: LCD may not light if SPI GPIO init failed (frame is always written to disk). Backpack LED is sent on the spine keepalive. Physical CHARGE-LATCH is wired to real button/lift now — confirm on the charger.

## After Phase 2

### Phase 0 leftovers (do when hardware allows)

- First flash from `ble-bootstrap` with our HTTP `.ota` (needs BLE radio + Phase 4 image)
- Physical CHARGE-LATCH on the charger (needs spine ownership from Phase 1)
- Hub hostname `robot.mohammadabbasi.com` on a same-LAN laptop (this VM is NAT’d; robot cannot ping it)

## Phase 2 — done on Vector-W1V9 (wheels gated)

On-robot veto owns motors. Hub sends skills only. `explore.enabled` is required before any wheel PWM; on charger wheels stay 0.

- Veto: front cliffs, ToF while forward, pickup/fall, hub heartbeat 250 ms, command TTL, `BATT_STOP_MV=3450`
- Cliff calibration → `/data/victor/cliffs.cal`
- Hub explorer IDLE/CREEP/LOOK/TURN/BACK_OFF/DOCK at 50 mm/s class
- Hub death → heartbeat veto, motors 0
- Desk-edge with hub dead: force-cliffs → `veto=cliff`, motors 0

Do **not** create `/data/victor/explore.enabled` on a live desk until Phase 2 has been proven on the floor/blocks.

### Phase 3 — camera, faces, voice

- Camera + mics (VCT1 VIDEO/AUDIO)
- Face DB in SQLite, names via `:8080/faces`
- Thinking bar on LCD
- OpenAI STT → `OPENAI_MODEL` → TTS PCM (key on hub only)
- Tiny NVIDIA ONNX edge model on hub (CPU first); votes `stop`/`back_off`; IR cliffs still win

### Phase 4 — our Yocto OTA

- Vendor kernel + our rootfs + `victor-agent`
- A/B slot, recoveryfs kept, HTTP `ota-start` from recovery
- First boot: SSH ON, BLE masked, `/data` `rw,exec`

## Definition of done (not yet)

- [x] No OpenAI key on the robot
- [ ] `docker compose up` starts `hub` **and** the robot talks to it
- [ ] `/etc/hosts` maps `robot.mohammadabbasi.com` to the laptop
- [ ] SENSOR ≥ 20 Hz plus audio/video at spec rates
- [ ] Killing hub stops wheels within 250 ms
- [ ] Desk-edge veto without hub
- [ ] Low-speed desk wander
- [ ] Speech with thinking bar + spoken reply
- [ ] Named face spoken and shown on the LCD
- [ ] SSH works before agent, after agent, and after reboot
