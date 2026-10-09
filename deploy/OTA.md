# Building and flashing the Victor `.ota`

Spec: [GROK_INSTRUCTIONS.md](../GROK_INSTRUCTIONS.md). The `.ota` writes only the
**inactive** boot + system slot through recovery `ota-start` over **HTTP**.
SBL, ABOOT and recoveryfs are never touched, so recovery (and its BLE) stays as
the unbrick path.

## What the image contains on first boot

`deploy/make-ota.sh` copies your system image and overlays it (debugfs: no root,
no loop mount), then verifies the result before packing:

| Requirement | How |
|---|---|
| SSH ON at first boot | SSH unit enabled + unmasked (`sshd.socket`, else `dropbear.service`/`.socket`, same order as the agent's `sshctl`). `victor-firstboot` writes `/data/victor/ssh.enabled=1` **only if missing**, so the CHARGE-LATCH state persists across reboots and later OTAs |
| Your key | `keys/ssh_root_key.pub` (or `--pubkey`) into `/usr/share/victor/authorized_keys`, root's `~/.ssh/authorized_keys` (0600, root) and, at boot, any `AuthorizedKeysFile` on `/data`. Private keys are refused |
| BLE off on the running image | `ankibluetoothd`, `btproperty`, `vic-switchboard`, `bluetooth` masked (`-> /dev/null`) in the image; `victor-ble-mask` also runs `rfkill block bluetooth` and writes `/data/victor/ble.disabled`. Recoveryfs is a different partition and keeps BLE |
| CHARGE-LATCH + SSH watchdog | `/usr/bin/victor-agent` (enabled service). Agent owns the spine on first boot (`/data/victor/anki.masked`) so it can see the button and lift; `--no-own-spine` opts out. Watchdog: SSH off >= 24 h (persisted via the flag mtime) AND hub heartbeat missing 10 min AND on charger -> SSH on, face `SSH AUTO` |
| Hub hostname | `/data/victor/hub.env` seeded with `HUB_HOST=robot.mohammadabbasi.com`; `--hub-ip IP` also writes the managed `/etc/hosts` block. Existing `hub.env` is kept |
| `/data` rw,exec | `/etc/fstab` `/data` line rewritten to `rw,exec`; firstboot also remounts |
| No OpenAI key | Build fails if your `.env` `OPENAI_API_KEY` (or any `OPENAI_API_KEY=value` / `sk-proj-` key) is anywhere in the system image; firstboot strips `OPENAI*` lines from `hub.env` |
| Traceability | `/etc/victor-release` (version, git commit, build time, SSH unit) |

**Do not ship WireOS as the product.** `--from-robot` repackages whatever system
image is running on the robot now (for first bring-up). The product image is the
bitbake `victor-image` ext4 passed with `--sysfs`.

## Prerequisites (your Linux machine)

```bash
sudo apt install e2fsprogs openssl gzip tar coreutils make golang-go openssh-client python3 libsodium-dev
make ota-check            # preflight: tells you exactly what is missing
```

Go >= 1.22 (`go version`). `libsodium-dev` + a BLE adapter are only needed for
`ble-bootstrap` / `first-flash`. Files that stay local (gitignored, never commit):
`keys/ssh_root_key`, `keys/ota.pas`, `.env`, `ota-work/`, `dist/`.

## 1. Build

From the robot you can SSH into today (dumps the active slot + `/anki/etc/ota.pas`):

```bash
cp .env.example .env      # ROBOT_SSH_IP, HUB_IP, WIFI_*, VECTOR_BLE_PIN
make ota OTA_ARGS="--from-robot --hub-ip 192.168.0.202"
```

From images you already have (bitbake `victor-image`, or the dumps kept in `ota-work/`):

```bash
scp -O -i keys/ssh_root_key root@$ROBOT_SSH_IP:/anki/etc/ota.pas keys/ota.pas   # once
make ota OTA_ARGS="--boot ota-work/boot.img --sysfs path/to/victor-image.ext4 --hub-ip 192.168.0.202"
```

Output: `dist/victor.ota` + `dist/victor.ota.sha256`. Options: `deploy/make-ota.sh --help`.

Rollback image of the untouched dump (keep it next to the new one):

```bash
./deploy/make-ota.sh --raw --boot ota-work/boot.img --sysfs ota-work/sysfs.robot.img --out dist/rollback.ota
```

## 2. Serve (HTTP, not HTTPS)

`first-flash --ota-file` serves it for you. To serve by hand:

```bash
make ota-serve            # = ./deploy/serve-ota.sh dist/victor.ota  (port 8088)
```

The robot must reach that URL on the same 2.4 GHz LAN; open the port if a
firewall is on (`sudo ufw allow 8088/tcp`).

## 3. Flash from recovery (BLE, once)

1. Robot on the charger. Hold the backpack button ~15 s until the rear lights are dark blue (recovery).
2. Double-press the button so it advertises `Vector-XXXX`; note the PIN on the face.
3. On the machine with a BLE adapter:

```bash
./deploy/first-flash --pin <PIN> --ssid <2.4GHz SSID> --password <wifi pass> --ota-file dist/victor.ota
# or, with make ota-serve running:
./deploy/first-flash --pin <PIN> --ssid <SSID> --password <pass> --url http://<lan-ip>:8088/victor.ota
```

`first-flash` = advertise -> PIN -> Wi-Fi -> `ota-start` (HTTP) -> reboot -> probe `:22`
-> `ssh root@$ROBOT_SSH_IP true` -> mask BLE. If the OTA stalls:
`./deploy/ble-bootstrap/ble-bootstrap get-status --pin <PIN>` / `ota-cancel`.
If the wrong interface IP is picked for `--ota-file`, set `OTA_HOST_IP=<lan-ip>`.

## 4. Verify

```bash
./deploy/ssh.sh true
./deploy/verify-first-boot.sh   # victor-release, ssh.enabled=1, BLE masked/rfkill, agent active, /data exec, no OpenAI key
./deploy/prove-phase0.sh
```

Then on the charger do CHARGE-LATCH (double-click, lift up >= 400 ms, lift down,
triple-click): face shows `SSH OFF`, port 22 closes, telemetry keeps flowing.
Repeat to turn SSH back on.

## Rollback / unbrick

- Recovery is never overwritten. Hold the button ~15 s on the charger -> recovery
  (BLE works there) -> `first-flash ... --ota-file dist/rollback.ota` (or a
  previous good `victor.ota`).
- SSH locked out but robot boots: CHARGE-LATCH on the charger, or wait for the
  watchdog (24 h off + hub silent 10 min + on charger -> `SSH AUTO`).
- Give the body back to stock Anki: `./deploy/restore-anki.sh` (the flag stays removed across reboots).

## Offline test (no robot)

```bash
make ota-test     # dummy ext4 sysfs + boot + ota.pas -> packer -> unpack/decrypt/verify
make prove4       # recipes + the same test
```
