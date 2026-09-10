# GROK_INSTRUCTIONS.md

Master build spec for coding agents working in `https://github.com/BrutalHex/victor`.

Owner: Mohammad Abbasi (`BrutalHex`). Hub hostname: `robot.mohammadabbasi.com`.

Do not ship WireOS as the product. Community trees are hardware reference only.

## BLE is first-flash only

Vector has no network until BLE pairing. After our image answers on SSH, **disable BLE on the running system**. Recoveryfs may keep BLE for unbrick only.

Facts:

- BLE only, not classic Bluetooth.
- Pair: on charger, double-press backpack button, enter face PIN.
- Recovery (first OTA): on charger, hold button ~15 s until rear lights are dark blue. Then double-press to advertise.
- Protocol: wrap `digital-dream-labs/vector-bluetooth` (RTS). Do not depend on Chrome Web Bluetooth as the shipping tool.

Implement `deploy/ble-bootstrap`:

| Verb | Role |
|---|---|
| scan / connect | Find `Vector-XXXX`, accept `--pin` |
| wifi-scan / wifi-connect / wifi-ip | 2.4 GHz from `.env`, write `ROBOT_SSH_IP` |
| ota-start | Recovery only. HTTP URL of our `.ota` (not HTTPS) |
| ota-cancel / get-status | Stall handling |

`first-flash` script: recovery advertise → PIN → wifi → HTTP host OTA → `ota-start` → reboot → probe `:22` → `ssh root@$ROBOT_SSH_IP true` → mask BLE units / `rfkill block bluetooth` / `/data/victor/ble.disabled`.

Yocto first boot: sshd on, BLE units masked.

## CHARGE-LATCH (SSH without BLE)

SSH is the only admin door after first flash. Toggle it with the body so BLE can stay dead.

Valid only when spine `on_charger` is true. Ignore the phrase while driving.

```
1. Double-click backpack button
2. Within 2 s: lift to the top, hold ≥ 400 ms
3. Lower lift to the bottom
4. Within 2 s: triple-click backpack button
```

Whole phrase ≤ 6 s. Click debounce 40–250 ms.

Effect:

- Flip `/data/victor/ssh.enabled`
- Start/stop dropbear listen on `:22` (do not kill `victor-agent`)
- Face: `SSH ON` green / `SSH OFF` red for 1.5 s
- Backpack LED: green pulse = listening

Defaults:

- First boot of our OTA: SSH ON so bootstrap can finish.
- Latch state persists across reboot.
- Outbound hub sockets to `robot.mohammadabbasi.com` keep running when SSH is off.

Watchdog: if SSH has been off 24 h AND hub heartbeat missing 10 min AND on charger → auto-enable SSH, face `SSH AUTO`.

15 s button hold still enters stock recovery (BLE allowed there only).

## Safety and hub (unchanged)

On-robot veto owns motors. Hub may send skills only. OpenAI stays on the hub. See prior sections for `VCT1` UDP, gRPC `:7443`, ports 7500–7502, Yocto A/B rules.

## Phase 0

Implement `ble-bootstrap`, prove first OTA, prove SSH, mask BLE, prove CHARGE-LATCH on the charger.

## Done when

- First flash works from `deploy/ble-bootstrap` with no phone app.
- Running image does not advertise BLE.
- CHARGE-LATCH toggles port 22.
- Telemetry survives SSH off.
- No OpenAI key on the robot.
