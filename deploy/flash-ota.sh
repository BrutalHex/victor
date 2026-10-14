#!/usr/bin/env bash
# Guarded wrapper around deploy/first-flash for `make flash`.
# Refuses without dist/victor.ota AND dist/rollback.ota, prints the recovery
# button steps, asks for confirmation, never prints the Wi-Fi password.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OTA="${OTA_FILE:-${ROOT}/dist/victor.ota}"
ROLLBACK="${ROLLBACK_FILE:-${ROOT}/dist/rollback.ota}"

die() { echo "flash: $*" >&2; exit 2; }
envval() { [[ -f "${ROOT}/.env" ]] && sed -n "s/^$1=//p" "${ROOT}/.env" | tail -n1 || true; }

[[ -s "$OTA" ]] || die "$OTA missing. Build it first: make ota-robot"
[[ -s "$ROLLBACK" ]] || die "$ROLLBACK missing. A rollback must exist before flashing: make ota-rollback"
for f in "$OTA" "$ROLLBACK"; do
  [[ "$(tar -tf "$f" 2>/dev/null | head -n1)" == manifest.ini ]] || die "$f is not a valid .ota"
done
if [[ -f "${OTA}.sha256" ]] && [[ "$(sha256sum "$OTA" | awk '{print $1}')" != "$(cat "${OTA}.sha256")" ]]; then
  die "$OTA does not match ${OTA}.sha256 (rebuild it)"
fi

PIN="${FLASH_PIN:-$(envval VECTOR_BLE_PIN)}"
SSID="${FLASH_WIFI_SSID:-$(envval WIFI_SSID)}"
PASS_SET=no
[[ -n "${FLASH_WIFI_PASSWORD:-}" || -n "$(envval WIFI_PASSWORD)" ]] && PASS_SET=yes
[[ -n "$SSID" ]] || die "no Wi-Fi SSID: make flash SSID=<2.4GHz SSID> (or WIFI_SSID in .env)"
[[ "$PASS_SET" == yes ]] || echo "flash: warning: no Wi-Fi password set (PASSWORD=... or WIFI_PASSWORD in .env)" >&2

cat <<STEPS

About to flash $(basename "$OTA") via recovery (BLE, HTTP).
  OTA:       $OTA ($(wc -c < "$OTA") bytes)
  Rollback:  $ROLLBACK
  Wi-Fi:     ${SSID} (password: ${PASS_SET/yes/set, hidden})
  PIN:       ${PIN:-<will need it: shown on the face after double-press>}

Put the robot in recovery now:
  1. Robot on the charger.
  2. Hold the backpack button ~15 s until the rear lights are dark blue.
  3. Double-press the button so it advertises Vector-XXXX; the face shows the PIN.
This machine needs a BLE adapter and must be on the same LAN as the robot.
Only boot+system (inactive slot) are written; recovery is kept for unbrick.

STEPS

if [[ -z "$PIN" ]]; then
  read -r -p "PIN shown on the face: " PIN </dev/tty
  [[ -n "$PIN" ]] || die "no PIN"
fi
if [[ "${YES:-}" != 1 ]]; then
  read -r -p "Type FLASH to start: " ans </dev/tty
  [[ "$ans" == FLASH ]] || die "aborted"
fi

FLASH_PIN="$PIN" FLASH_WIFI_SSID="$SSID" exec "${ROOT}/deploy/first-flash" --ota-file "$OTA"
