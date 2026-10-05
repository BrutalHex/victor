#!/usr/bin/env bash
# Prove Phase 4 recipes + OTA packer. Does not bitbake or flash.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

need() { [[ -f "$1" ]] || fail "missing $1"; }

echo "== recipes =="
need "${ROOT}/yocto/meta-victor/recipes-core/images/victor-image.bb"
need "${ROOT}/yocto/meta-victor/recipes-core/images/victor-image-recovery.bb"
need "${ROOT}/yocto/meta-victor/recipes-core/victor-firstboot/files/victor-firstboot.sh"
need "${ROOT}/yocto/meta-victor/recipes-core/victor-hosts/files/victor-set-hub-ip"
need "${ROOT}/yocto/meta-victor/recipes-core/base-files/base-files_%.bbappend"
pass "image + firstboot + hosts + fstab"

echo "== running image masks BLE; recovery does not =="
grep -q victor-ble-mask "${ROOT}/yocto/meta-victor/recipes-core/images/victor-image.inc" || fail "ble-mask not in running image"
if grep -E 'IMAGE_INSTALL' "${ROOT}/yocto/meta-victor/recipes-core/images/victor-image-recovery.bb" | grep -q victor-ble-mask; then
  fail "recoveryfs must keep BLE"
fi
pass "BLE split"

echo "== first boot SSH ON =="
grep -q ssh.enabled "${ROOT}/yocto/meta-victor/recipes-core/victor-firstboot/files/victor-firstboot.sh" || fail "ssh.enabled"
grep -q 'rw,exec' "${ROOT}/yocto/meta-victor/recipes-core/base-files/base-files_%.bbappend" || fail "/data exec"
pass "first boot flags"

echo "== dummy OTA =="
BOOT=$(mktemp)
SYS=$(mktemp)
dd if=/dev/zero of="$BOOT" bs=1024 count=8 status=none
dd if=/dev/zero of="$SYS" bs=1024 count=16 status=none
make -C "$ROOT" agent-arm >/dev/null
OUT=$(mktemp --suffix=.ota)
"${ROOT}/deploy/make-ota.sh" --boot "$BOOT" --sysfs "$SYS" --out "$OUT" --version 0.0.0-test
tar -tf "$OUT" | grep -q manifest.ini || fail "manifest"
tar -tf "$OUT" | grep -q apq8009-robot-boot.img.gz || fail "boot image"
tar -tf "$OUT" | grep -q apq8009-robot-sysfs.img.gz || fail "sysfs image"
tar -tf "$OUT" | grep -q victor-overlay.tar.gz || fail "overlay"
tar -xOf "$OUT" manifest.ini | grep -q keep_recoveryfs=1 || fail "recovery kept"
tar -xOf "$OUT" manifest.ini | grep -q target_slot=inactive || fail "A/B inactive"
rm -f "$BOOT" "$SYS" "$OUT"
pass "make-ota.sh HTTP payload"

echo "== first-flash still HTTP-only =="
grep -q 'cannot pull HTTPS' "${ROOT}/deploy/first-flash" || fail "first-flash https guard"
pass "ota-start is HTTP"

echo
echo "Phase 4 recipes and OTA packer proved (no flash; needs vendor kernel + bitbake to ship)."
