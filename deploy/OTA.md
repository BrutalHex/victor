# Building and flashing the Victor `.ota`

Spec: [GROK_INSTRUCTIONS.md](../GROK_INSTRUCTIONS.md). The `.ota` writes only the
**inactive** boot + system slot through recovery `ota-start` over **HTTP**.
SBL, ABOOT and recoveryfs are never touched, so recovery (and its BLE) stays as
the unbrick path.

## Quick start (two commands)

```bash
make ota-deps          # once: apt/dnf install (asks for sudo) + preflight
make release           # preflight -> dump robot + build dist/victor.ota + dist/rollback.ota
                       # -> recovery flash (asks you to type FLASH) -> verify
```

`make release` reads `.env` for `HUB_IP`, `VECTOR_BLE_PIN`, `WIFI_SSID`, `WIFI_PASSWORD`
(copy `.env.example`). Override anything on the command line, e.g.
`make release HUB_IP=192.168.0.50 PIN=123456 SSID=home`. If `HUB_IP` is not set
anywhere, the LAN IP of the default route is used. The Wi-Fi password is never
printed and is passed to `ble-bootstrap` through the environment, not argv.
`PASSWORD=...` on the command line works but lands in your shell history; prefer `.env`.

| Target | Does |
|---|---|
| `make ota-deps` | install e2fsprogs, openssl, gzip, tar, make, Go, openssh-client, python3, libsodium-dev, iproute2; check Go >= 1.22; run `ota-check` |
| `make ota-check` | preflight only |
| `make ota-robot [HUB_IP=..]` | dump the active slot + `ota.pas` from the robot, build `dist/victor.ota`, then `ota-rollback` |
| `make ota-rollback` | `dist/rollback.ota` from the untouched dump in `ota-work/` (first one also kept as `dist/rollback-first.ota`, never overwritten) |
| `make ota OTA_ARGS=".."` | packer with your own args (e.g. bitbake `--boot/--sysfs`) |
| `make ota-serve` | plain HTTP on :8088 for a manual `ota-start` |
| `make flash [PIN=..] [SSID=..] [PASSWORD=..]` | refuses unless `dist/victor.ota` and `dist/rollback.ota` exist, prints the recovery button steps, asks you to type `FLASH` (`YES=1` skips), runs `first-flash` |
| `make verify` | `deploy/verify-first-boot.sh` |
| `make release` / `make ota-all` | `ota-check`, `ota-robot`, `flash`, `verify` in order |
| `make ota-test` | offline packer test, no robot |
| `make help` | list targets |

The sections below are what those targets do, step by step.

## What the image contains on first boot

`deploy/make-ota.sh` copies your system image and overlays it (debugfs: no root,
no loop mount), then verifies the result before packing:

| Requirement | How |
|---|---|
| SSH ON at first boot | SSH unit enabled + unmasked (`sshd.socket`, else `dropbear.service`/`.socket`, same order as the agent's `sshctl`). `victor-firstboot` writes `/data/victor/ssh.enabled=1` **only if missing**, so the SSH state set by voice persists across reboots and later OTAs |
| Your key | `keys/ssh_root_key.pub` (or `--pubkey`) into `/usr/share/victor/authorized_keys`, root's `~/.ssh/authorized_keys` (0600, root) and, at boot, any `AuthorizedKeysFile` on `/data`. Private keys are refused |
| BLE off on the running image | `ankibluetoothd`, `btproperty`, `vic-switchboard`, `bluetooth` masked (`-> /dev/null`) in the image; `victor-ble-mask` also runs `rfkill block bluetooth` and writes `/data/victor/ble.disabled`. Recoveryfs is a different partition and keeps BLE |
| Voice SSH toggle + SSH watchdog | `/usr/bin/victor-agent` (enabled service) runs the hub's `ssh_on` / `ssh_off` / `ssh_status` voice actions (owner's request, 10 Oct 2026; the CHARGE-LATCH button gesture is off unless `/data/victor/charge-latch.enabled` exists). Agent owns the spine on first boot (`/data/victor/anki.masked`); `--no-own-spine` opts out. Watchdog: SSH off >= 24 h (persisted via the flag mtime) AND hub heartbeat missing 10 min AND on charger -> SSH on, face `SSH AUTO` |
| Hub hostname | `/data/victor/hub.env` seeded with `HUB_HOST=robot.mohammadabbasi.com`; `--hub-ip IP` also writes the managed `/etc/hosts` block. Existing `hub.env` is kept |
| `/data` rw,exec | `/etc/fstab` `/data` line rewritten to `rw,exec`; firstboot also remounts |
| No OpenAI key | Build fails if your `.env` `OPENAI_API_KEY` (or any `OPENAI_API_KEY=value` / `sk-proj-` key) is anywhere in the system image; firstboot strips `OPENAI*` lines from `hub.env` |
| Image recognition (robot side) | `victor-agent` streams camera JPEGs as VCT1 VIDEO to `HUB_HOST` (nav 320x180 ~10 Hz, face 640x360 ~5 Hz). Camera units in the image are never masked; the packer fails if one is |
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

## 3b. Flash over SSH (robot already runs an unlocked image, no BLE)

```bash
make flash-ssh      # = ./deploy/flash-ssh.sh --reboot
```

Serves the `.ota` on `127.0.0.1` and tunnels it to the robot (`ssh -R`, so it
works from WSL behind NAT), stops the hourly auto-update, runs the robot's
`/anki/bin/update-engine`, which writes the **inactive** slot (`boot_b` +
`system_b` when running on `_a`) and marks it active, reads both partitions
back and compares sha256 with the manifest, then reboots. Refuses unless the
robot is on slot `_a` (`ALLOW_FROM_SLOT=_b` to override), so the known-good
slot is never overwritten by default. WireOS 3.0.9.1d's update-engine does not
check signatures or sha256 itself; the packer and the readback do.

Back to the previous slot (no BLE needed while SSH works):
`ssh root@$ROBOT_SSH_IP '/bin/bootctl-anki b set_active a; reboot'`

## 4. Verify

```bash
./deploy/ssh.sh true
./deploy/verify-first-boot.sh   # victor-release, ssh.enabled=1, BLE masked/rfkill, agent active, /data exec, no OpenAI key
./deploy/prove-phase0.sh
```

Then say "Vector, disable SSH": Vector says "SSH is off", the face shows `SSH OFF`,
port 22 closes, telemetry keeps flowing. "Vector, enable SSH" turns it back on
("Is SSH on?" asks). German and Persian work too (README "First flash and SSH").

## Rollback / unbrick

- Recovery is never overwritten. Hold the button ~15 s on the charger -> recovery
  (BLE works there) -> `first-flash ... --ota-file dist/rollback.ota` (or a
  previous good `victor.ota`).
- SSH locked out but robot boots: say "Vector, enable SSH" (the hub must be up),
  or wait for the watchdog (24 h off + hub silent 10 min + on charger -> `SSH AUTO`).
- Give the body back to stock Anki: `./deploy/restore-anki.sh` (the flag stays removed across reboots).

## Image recognition

Recognition runs **on the hub only**; the robot ships frames. There is no other
vision API and no cloud key on the robot.

| Piece | Where | State |
|---|---|---|
| Camera capture | robot, `victor-agent` `internal/camera` | Client of the stock camera daemon `mm-anki-camera` (unix socket `/var/run/mm-anki-camera/camera-server`, ION shared buffer, RGB888 640x360). Sends 320x180 nav JPEGs at 10 fps and 640x360 face JPEGs at 5 fps. `/data/victor/camera.jpg` still overrides for tests. Colour/orientation: `/data/victor/camera.conf` (`swap_rb`, `flip`, `gamma`). Hub `GET /frame?kind=face` returns the latest frame |
| VCT1 VIDEO | robot -> hub UDP 7500 (+ TCP 7443 link) | shipped in the agent; endpoint from `hub.env` / `robot.mohammadabbasi.com` |
| Faces | hub `faces.py`, SQLite, `GET/POST/DELETE :8080/faces` | simple 64-d grayscale-grid embedding + cosine >= 0.92 on the whole face frame; no face detector yet |
| Edge model | hub `edge.py`, ONNX Runtime CPU, `hub/models/*.onnx` | `tiny_edge.onnx` placeholder generated at container start; drop a real NVIDIA TAO ONNX (<= 15 MB) in `hub/models/`. Votes `stop`/`back_off` only; IR cliffs on the robot win |
| OpenAI | hub only (voice) | never on the robot |

## Offline test (no robot)

```bash
make ota-test     # dummy ext4 sysfs + boot + ota.pas -> packer -> unpack/decrypt/verify
make prove4       # recipes + the same test
```
