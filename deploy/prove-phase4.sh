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
grep -q 'victor-firstboot.service' "${ROOT}/yocto/meta-victor/recipes-core/victor-agent/files/victor-agent.service" || fail "agent after firstboot"
pass "first boot flags"

echo "== OTA packer end-to-end (dummy images, no flash) =="
"${ROOT}/deploy/test-ota.sh"
pass "make-ota.sh HTTP payload"

echo "== first-flash still HTTP-only =="
grep -q 'cannot pull HTTPS' "${ROOT}/deploy/first-flash" || fail "first-flash https guard"
pass "ota-start is HTTP"

echo
echo "Phase 4 recipes and OTA packer proved (no flash; needs vendor kernel + bitbake to ship)."
