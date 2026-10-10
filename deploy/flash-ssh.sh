#!/usr/bin/env bash
# Flash dist/victor.ota from a RUNNING image over SSH (no BLE needed).
# Serves the .ota on this machine's 127.0.0.1 and reaches it from the robot
# through an SSH reverse tunnel (works behind WSL NAT), then runs the robot's
# own /anki/bin/update-engine, which writes the INACTIVE slot (boot_X +
# system_X) and marks it active. The current slot stays as fallback:
#   ssh root@robot '/bin/bootctl-anki <cur> set_active <old>; reboot'
# Refuses without a rollback, on a sha mismatch, or if the robot is not on
# slot a (so slot a, the known-good image, is never overwritten by default).
# Does not reboot unless --reboot. Used for the first real flash (2026-10-09).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"
OTA="${OTA_FILE:-${ROOT}/dist/victor.ota}"
ROLLBACK="${ROLLBACK_FILE:-${ROOT}/dist/rollback.ota}"
PORT="${OTA_TUNNEL_PORT:-8099}"
ALLOW_SLOT="${ALLOW_FROM_SLOT:-_a}"
REBOOT=0
[[ "${1:-}" == "--reboot" ]] && REBOOT=1

die() { echo "flash-ssh: $*" >&2; exit 3; }
[[ -s "$OTA" ]] || die "$OTA missing (make ota-robot)"
[[ -s "$ROLLBACK" ]] || die "$ROLLBACK missing (make ota-rollback)"
[[ ! -f "${OTA}.sha256" || "$(sha256sum "$OTA" | awk '{print $1}')" == "$(cat "${OTA}.sha256")" ]] || die "sha mismatch"
[[ "$(tar -tf "$OTA" | head -n1)" == manifest.ini ]] || die "not an .ota"
want_sys="$(tar -xOf "$OTA" manifest.ini | awk -F= '/^\[SYSTEM\]/{s=1} s&&/^sha256/{print $2}')"
want_boot="$(tar -xOf "$OTA" manifest.ini | awk -F= '/^\[BOOT\]/{s=1} s&&/^sha256/{print $2; exit}')"

python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$(dirname "$OTA")" >/dev/null 2>&1 &
HP=$!
trap 'kill $HP 2>/dev/null || true' EXIT
sleep 1
URL="http://127.0.0.1:${PORT}/$(basename "$OTA")"

ssh "${SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
  -R "127.0.0.1:${PORT}:127.0.0.1:${PORT}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" \
  "ALLOW='${ALLOW_SLOT}' URL='${URL}' WANT_SYS='${want_sys}' WANT_BOOT='${want_boot}' REBOOT='${REBOOT}' sh -s" <<'ROBOT'
set -e
SFX=$(tr " " "\n" </proc/cmdline | sed -n "s/^androidboot.slot_suffix=//p")
echo "current slot: $SFX"
[ "$SFX" = "$ALLOW" ] || { echo "refusing: robot is on $SFX (ALLOW_FROM_SLOT=$ALLOW)"; exit 3; }
case "$SFX" in _a) T=b ;; _b) T=a ;; *) echo "unknown slot"; exit 3 ;; esac
# the hourly auto-update would wipe /run/update-engine mid-flash
systemctl stop update-engine.timer update-engine.service 2>/dev/null || true
curl -sfI "$URL" >/dev/null || { echo "robot cannot reach $URL"; exit 3; }
echo "writing boot_$T + system_$T ..."
/anki/bin/update-engine "$URL" -v > /data/victor/ota-flash.log 2>&1 || { tr "\r" "\n" </data/victor/ota-flash.log | tail -n 5; exit 4; }
tr "\r" "\n" </data/victor/ota-flash.log | grep -v "^progress" | tail -n 2
B=$(sha256sum /dev/block/bootdevice/by-name/boot_$T | cut -d" " -f1)
S=$(sha256sum /dev/block/bootdevice/by-name/system_$T | cut -d" " -f1)
[ "$B" = "$WANT_BOOT" ] && [ "$S" = "$WANT_SYS" ] || { echo "READBACK MISMATCH boot=$B system=$S"; /bin/bootctl-anki "${SFX#_}" set_active "${SFX#_}"; exit 5; }
echo "readback ok; slot $T active on next boot:"; /bin/bootctl-anki "${SFX#_}" status "$T"
if [ "$REBOOT" = 1 ]; then sync; (sleep 2; /sbin/reboot) >/dev/null 2>&1 & fi
ROBOT
echo "done. After reboot: make verify. Back to the old slot: ssh in and run bootctl-anki <cur> set_active <old>; reboot"
