#!/usr/bin/env bash
# Offline end-to-end test of deploy/make-ota.sh. No robot, no BLE, no flash.
# Builds a dummy ext4 sysfs (stock-like: sshd.socket, BLE units, /data noexec,
# a stale sync-agent override), a dummy boot image and a dummy ota.pas, runs
# the packer, then unpacks the .ota exactly like update-engine would and checks
# layout, manifest sizes/sha256, decryptability and the sysfs contents.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="${PATH}:/sbin:/usr/sbin"
export DEBUGFS_PAGER=__none__ PAGER=cat

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }
for t in mkfs.ext4 debugfs e2fsck openssl gzip tar sha256sum ssh-keygen go; do
  command -v "$t" >/dev/null || fail "missing tool $t (sudo apt install e2fsprogs openssl gzip tar openssh-client golang-go)"
done

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
FAKE_KEY="sk-proj-TESTONLYdoNotShipThisKey0123456789abcdef"

# --- dummy inputs
mkdir -p "$T/root"/{etc/systemd/system/multi-user.target.wants,lib/systemd/system,home/root,usr/bin,anki/etc}
cat > "$T/root/etc/passwd" <<'P'
root:x:0:0:root:/home/root:/bin/sh
P
printf '/dev/root / ext4 ro 0 1\n/dev/disk/by-partlabel/userdata /data ext4 rw,noexec,nosuid 0 2\n' > "$T/root/etc/fstab"
printf '127.0.0.1\tlocalhost\n' > "$T/root/etc/hosts"
for u in sshd.socket ankibluetoothd.service vic-switchboard.service btproperty.service mm-anki-camera.service; do
  printf '[Unit]\nDescription=%s\n' "$u" > "$T/root/lib/systemd/system/$u"
done
# stale override left by deploy/sync-agent.sh on the running robot
printf '[Service]\nExecStart=/data/victor/victor-agent run\n' > "$T/root/etc/systemd/system/victor-agent.service"
ln -s /lib/systemd/system/ankibluetoothd.service "$T/root/etc/systemd/system/multi-user.target.wants/ankibluetoothd.service"
mkfs.ext4 -q -F -E root_owner=0:0 -L system -d "$T/root" "$T/sysfs.img" 64M
SYS_IN_SHA="$(sha256sum "$T/sysfs.img" | awk '{print $1}')"
{ printf 'ANDROID!'; head -c 262136 /dev/urandom; } > "$T/boot.img"
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$T/ota.pas"
ssh-keygen -q -t ed25519 -N '' -C test@victor -f "$T/id" >/dev/null
pass "dummy sysfs/boot/pass/key"

# --- negative: a private key must be refused
if OPENAI_API_KEY="" "${ROOT}/deploy/make-ota.sh" --boot "$T/boot.img" --sysfs "$T/sysfs.img" \
     --pass "$T/ota.pas" --pubkey "$T/id" --out "$T/bad.ota" --work "$T/work" >"$T/neg.log" 2>&1; then
  fail "packer accepted a private key"
fi
grep -q 'PRIVATE key' "$T/neg.log" || { cat "$T/neg.log"; fail "wrong error for private key"; }
pass "private key refused"

# --- build
OPENAI_API_KEY="$FAKE_KEY" "${ROOT}/deploy/make-ota.sh" --boot "$T/boot.img" --sysfs "$T/sysfs.img" \
  --pass "$T/ota.pas" --pubkey "$T/id.pub" --hub-ip 192.168.0.202 \
  --out "$T/out/victor.ota" --work "$T/work" --version 0.0.0-test > "$T/build.log" 2>&1 \
  || { cat "$T/build.log"; fail "make-ota.sh"; }
OTA="$T/out/victor.ota"
pass "packer ran"

[[ "$(sha256sum "$T/sysfs.img" | awk '{print $1}')" == "$SYS_IN_SHA" ]] || fail "input sysfs was modified"
pass "input sysfs untouched"

# --- tar layout (manifest first, exactly three members, no recovery/aboot)
mapfile -t MEMBERS < <(tar -tf "$OTA")
[[ "${MEMBERS[*]}" == "manifest.ini apq8009-robot-boot.img.gz apq8009-robot-sysfs.img.gz" ]] \
  || fail "tar members: ${MEMBERS[*]}"
pass "tar layout"

mkdir -p "$T/x"
tar -C "$T/x" -xf "$OTA"
M="$T/x/manifest.ini"
ini() { awk -F= -v s="[$1]" -v k="$2" '$0==s{f=1;next} /^\[/{f=0} f&&$1==k{print $2}' "$M"; }
[[ "$(grep -c '^\[' "$M")" == 3 ]] || fail "manifest sections"
grep -qiE 'recovery|aboot|sbl' "$M" && fail "manifest mentions recovery/aboot/sbl"
[[ "$(ini META num_images)" == 2 && "$(ini META update_version)" == 0.0.0-test && "$(ini META ankidev)" == 1 ]] || fail "META"
for s in BOOT SYSTEM; do
  [[ "$(ini $s encryption)" == 1 && "$(ini $s compression)" == gz && "$(ini $s wbits)" == 31 ]] || fail "$s fields"
done
pass "manifest fields"

dec() { openssl enc -d -aes-256-ctr -md md5 -pass file:"$T/ota.pas" -in "$1" 2>/dev/null | gzip -dc > "$2"; }
dec "$T/x/apq8009-robot-boot.img.gz" "$T/boot.out" || fail "boot decrypt"
dec "$T/x/apq8009-robot-sysfs.img.gz" "$T/sys.out" || fail "sysfs decrypt"
# wrong password must not decrypt to a valid gzip
head -c 32 /dev/urandom > "$T/wrong.pas"
if openssl enc -d -aes-256-ctr -md md5 -pass file:"$T/wrong.pas" -in "$T/x/apq8009-robot-boot.img.gz" 2>/dev/null | gzip -t 2>/dev/null; then
  fail "boot decrypts with a wrong password"
fi
cmp -s "$T/boot.out" "$T/boot.img" || fail "boot image changed"
[[ "$(wc -c < "$T/boot.out" | tr -d ' ')" == "$(ini BOOT bytes)" ]] || fail "BOOT bytes"
[[ "$(sha256sum "$T/boot.out" | awk '{print $1}')" == "$(ini BOOT sha256)" ]] || fail "BOOT sha256"
[[ "$(wc -c < "$T/sys.out" | tr -d ' ')" == "$(ini SYSTEM bytes)" ]] || fail "SYSTEM bytes"
[[ "$(sha256sum "$T/sys.out" | awk '{print $1}')" == "$(ini SYSTEM sha256)" ]] || fail "SYSTEM sha256"
pass "decrypt + gunzip match manifest bytes/sha256 (wrong pass rejected)"

# --- sysfs contents (from the decrypted OTA, not the work copy)
IMG="$T/sys.out"
e2fsck -fn "$IMG" >/dev/null 2>&1 || fail "decrypted sysfs not a clean ext4"
d() { debugfs -R "$1" "$IMG" 2>/dev/null; }
st() { d "stat $1"; }
linkof() { st "$1" | sed -n 's/^Fast link dest: "\(.*\)"$/\1/p'; }
isfile() { # path mode
  local s; s="$(st "$1")"
  [[ "$s" == *"Type: regular"* ]] || fail "$1 missing"
  [[ "$s" =~ Mode:\ +0?$2 ]] || fail "$1 mode (want $2)"
  [[ "$s" =~ User:\ +0\ +Group:\ +0 ]] || fail "$1 not root-owned"
}
isfile /usr/bin/victor-agent 755
cmp -s <(d "cat /usr/bin/victor-agent") "${ROOT}/robot/agent/dist/victor-agent" || fail "agent binary differs"
[[ "$(d 'cat /usr/bin/victor-agent' | od -An -tx1 -j18 -N2 | tr -d ' \n')" == 2800 ]] || fail "agent not ARM"
for f in victor-set-hub-ip victor-firstboot victor-ble-mask; do isfile "/usr/bin/$f" 755; done
pass "agent (CHARGE-LATCH + SSH watchdog) + scripts installed, root:root 0755"

for u in victor-agent victor-firstboot victor-ble-mask; do
  isfile "/lib/systemd/system/$u.service" 644
  [[ "$(linkof /etc/systemd/system/multi-user.target.wants/$u.service)" == "/lib/systemd/system/$u.service" ]] || fail "$u not enabled"
done
d 'cat /lib/systemd/system/victor-agent.service' | grep -q '^ExecStart=/usr/bin/victor-agent run' || fail "agent ExecStart"
d 'cat /lib/systemd/system/victor-firstboot.service' | grep -q '^Before=.*sshd.socket' && fail "firstboot Before=sshd.socket would cycle with mount-data"
d 'cat /lib/systemd/system/victor-firstboot.service' | grep -q '^After=.*mount-data.service' || fail "firstboot must wait for mount-data"
[[ -z "$(st /etc/systemd/system/victor-agent.service)" ]] || fail "stale /etc agent override kept"
pass "units installed + enabled; stale override removed"

[[ "$(linkof /etc/systemd/system/sockets.target.wants/sshd.socket)" == /lib/systemd/system/sshd.socket ]] || fail "sshd.socket not enabled"
[[ "$(linkof /etc/systemd/system/sshd.socket)" != /dev/null ]] || fail "ssh masked"
grep -q 'echo 1 > "$D/ssh.enabled"' <(d 'cat /usr/bin/victor-firstboot') || fail "first boot SSH ON default"
grep -q '\[ -f "$D/ssh.enabled" \] ||' <(d 'cat /usr/bin/victor-firstboot') || fail "ssh.enabled must persist (only seeded if missing)"
pass "SSH ON at first boot (sshd.socket enabled, ssh.enabled seeded only if missing)"

for u in ankibluetoothd vic-switchboard btproperty bluetooth; do
  [[ "$(linkof /etc/systemd/system/$u.service)" == /dev/null ]] || fail "$u not masked"
done
d 'cat /usr/bin/victor-ble-mask' | grep -q 'rfkill block bluetooth' || fail "rfkill"
d 'cat /usr/bin/victor-firstboot' | grep -q 'ble.disabled' || fail "ble.disabled"
pass "BLE masked (ankibluetoothd, vic-switchboard, btproperty, bluetooth), rfkill + ble.disabled"

# image recognition: camera stack untouched, agent streams VCT1 VIDEO to the hub
[[ -n "$(st /lib/systemd/system/mm-anki-camera.service)" ]] || fail "camera unit removed"
[[ "$(linkof /etc/systemd/system/mm-anki-camera.service)" != /dev/null ]] || fail "camera unit masked"
grep -q 'camera units untouched (mm-anki-camera.service)' "$T/build.log" || fail "packer did not check camera units"
LC_ALL=C grep -aq '/dev/video0' <(d 'cat /usr/bin/victor-agent') || fail "agent lacks V4L2 camera path"
LC_ALL=C grep -aq 'internal/camera' <(d 'cat /usr/bin/victor-agent') || fail "agent lacks camera package"
d 'cat /usr/share/victor/hub.env.default' | grep -qx 'HUB_GRPC_PORT=7443' || fail "hub media port"
pass "image recognition (robot side): camera unit kept, agent has camera + VCT1 VIDEO, hub endpoint set"

K="$(cat "$T/id.pub")"
[[ "$(d 'cat /usr/share/victor/authorized_keys')" == "$K" ]] || fail "share authorized_keys"
isfile /home/root/.ssh/authorized_keys 600
[[ "$(d 'cat /home/root/.ssh/authorized_keys')" == "$K" ]] || fail "root authorized_keys"
st /home/root/.ssh | grep -Eq 'Mode: +0?700' || fail ".ssh mode"
pass "public key authorized for root (0600, root-owned)"

d 'cat /etc/fstab' | grep -E '[[:space:]]/data[[:space:]]' | grep -q 'rw,exec' || fail "/data rw,exec in fstab"
d 'cat /etc/fstab' | grep -E '[[:space:]]/data[[:space:]]' | grep -q noexec && fail "/data still noexec"
pass "/data rw,exec"

d 'cat /etc/hosts' | grep -Eq '^192\.168\.0\.202 +robot\.mohammadabbasi\.com hub$' || fail "hosts block"
d 'cat /etc/hosts' | grep -q '^127.0.0.1' || fail "hosts lost localhost"
d 'cat /usr/share/victor/hub.env.default' | grep -qx 'HUB_HOST=robot.mohammadabbasi.com' || fail "HUB_HOST"
d 'cat /usr/share/victor/hub.env.default' | grep -qx 'HUB_IP=192.168.0.202' || fail "HUB_IP"
d 'cat /etc/victor-release' | grep -q 'VICTOR_VERSION=0.0.0-test' || fail "victor-release"
[[ -n "$(st /usr/share/victor/own-spine)" ]] || fail "own-spine marker"
pass "hub hostname robot.mohammadabbasi.com, /etc/victor-release, own-spine"

LC_ALL=C grep -aq "$FAKE_KEY" "$IMG" && fail "OpenAI key leaked into sysfs"
LC_ALL=C grep -aq "$FAKE_KEY" "$OTA" && fail "OpenAI key leaked into .ota"
LC_ALL=C grep -aq 'PRIVATE KEY' "$IMG" && fail "a private key is in the sysfs"
pass "no OpenAI key, no private key in the image"

# --- negative: an image that contains the .env OpenAI key is refused
mkdir -p "$T/root2"; cp -a "$T/root/." "$T/root2/"
echo "OPENAI_API_KEY=$FAKE_KEY" > "$T/root2/etc/oops.env"
mkfs.ext4 -q -F -E root_owner=0:0 -d "$T/root2" "$T/sysfs2.img" 64M
if OPENAI_API_KEY="$FAKE_KEY" "${ROOT}/deploy/make-ota.sh" --boot "$T/boot.img" --sysfs "$T/sysfs2.img" \
     --pass "$T/ota.pas" --pubkey "$T/id.pub" --out "$T/bad.ota" --work "$T/work2" >"$T/neg2.log" 2>&1; then
  fail "packer shipped an image with an OpenAI key"
fi
grep -q 'OpenAI stays on the hub' "$T/neg2.log" || { cat "$T/neg2.log"; fail "wrong error for OpenAI key"; }
pass "image with an OpenAI key refused"

# --- negative: no SSH unit at all is refused
rm -f "$T/root2/etc/oops.env" "$T/root2/lib/systemd/system/sshd.socket"
mkfs.ext4 -q -F -E root_owner=0:0 -d "$T/root2" "$T/sysfs3.img" 64M
if OPENAI_API_KEY="" "${ROOT}/deploy/make-ota.sh" --boot "$T/boot.img" --sysfs "$T/sysfs3.img" \
     --pass "$T/ota.pas" --pubkey "$T/id.pub" --out "$T/bad.ota" --work "$T/work3" >"$T/neg3.log" 2>&1; then
  fail "packer built an image without SSH"
fi
grep -q 'no sshd.socket / dropbear' "$T/neg3.log" || { cat "$T/neg3.log"; fail "wrong error for missing ssh"; }
pass "image without an SSH unit refused"

# --- dropbear-only image (Yocto victor-image style)
printf '[Unit]\nDescription=dropbear\n' > "$T/root2/lib/systemd/system/dropbear.service"
mkfs.ext4 -q -F -E root_owner=0:0 -d "$T/root2" "$T/sysfs4.img" 64M
OPENAI_API_KEY="" "${ROOT}/deploy/make-ota.sh" --boot "$T/boot.img" --sysfs "$T/sysfs4.img" \
  --pass "$T/ota.pas" --pubkey "$T/id.pub" --out "$T/db.ota" --work "$T/work4" >"$T/db.log" 2>&1 \
  || { cat "$T/db.log"; fail "dropbear image"; }
tar -xOf "$T/db.ota" apq8009-robot-sysfs.img.gz | openssl enc -d -aes-256-ctr -md md5 -pass file:"$T/ota.pas" 2>/dev/null | gzip -dc > "$T/db.img"
[[ "$(debugfs -R 'stat /etc/systemd/system/multi-user.target.wants/dropbear.service' "$T/db.img" 2>/dev/null | sed -n 's/^Fast link dest: "\(.*\)"$/\1/p')" == /lib/systemd/system/dropbear.service ]] \
  || fail "dropbear.service not enabled"
pass "dropbear image: dropbear.service enabled on :22"

# --- negative: a masked camera unit (would break hub image recognition) is refused
ln -sf /dev/null "$T/root2/etc/systemd/system/mm-anki-camera.service"
mkfs.ext4 -q -F -E root_owner=0:0 -d "$T/root2" "$T/sysfs6.img" 64M
if OPENAI_API_KEY="" "${ROOT}/deploy/make-ota.sh" --boot "$T/boot.img" --sysfs "$T/sysfs6.img" \
     --pass "$T/ota.pas" --pubkey "$T/id.pub" --out "$T/bad.ota" --work "$T/work6" >"$T/neg6.log" 2>&1; then
  fail "packer shipped a masked camera unit"
fi
grep -q 'camera unit mm-anki-camera.service is masked' "$T/neg6.log" || { cat "$T/neg6.log"; fail "wrong error for masked camera"; }
rm -f "$T/root2/etc/systemd/system/mm-anki-camera.service"
pass "masked camera unit refused"

# --- --raw rollback pack: images byte-identical to the inputs
"${ROOT}/deploy/make-ota.sh" --raw --boot "$T/boot.img" --sysfs "$T/sysfs.img" \
  --pass "$T/ota.pas" --out "$T/raw.ota" --work "$T/work5" >"$T/raw.log" 2>&1 || { cat "$T/raw.log"; fail "--raw"; }
[[ "$(tar -xOf "$T/raw.ota" apq8009-robot-sysfs.img.gz | openssl enc -d -aes-256-ctr -md md5 -pass file:"$T/ota.pas" 2>/dev/null | gzip -dc | sha256sum | awk '{print $1}')" == "$SYS_IN_SHA" ]] \
  || fail "--raw changed the sysfs"
pass "--raw rollback .ota packs the pristine sysfs unchanged"

# --- victor-firstboot behaviour (run the shipped script against a fake root)
FB="$T/fb"
mkdir -p "$FB"/{data,share,etc,bin,home/root}
d 'cat /usr/bin/victor-firstboot' > "$FB/firstboot.orig"
sed -e "s#/usr/bin/victor-set-hub-ip#$FB/bin/victor-set-hub-ip#g" \
    -e "s#/usr/share/victor#$FB/share#g" \
    -e "s#/data#$FB/data#g" \
    -e "s#/etc/#$FB/etc/#g" \
    -e "s#/home/root#$FB/home/root#g" "$FB/firstboot.orig" > "$FB/firstboot"
printf '#!/bin/sh\nexec sh %s "$1" "$2" %s\n' "${ROOT}/robot/scripts/victor-set-hub-ip" "$FB/etc/hosts" > "$FB/bin/victor-set-hub-ip"
chmod +x "$FB/bin/victor-set-hub-ip"
printf '127.0.0.1\tlocalhost\n' > "$FB/etc/hosts"
echo "root:x:0:0:root:$FB/home/root:/bin/sh" > "$FB/etc/passwd"
mkdir -p "$FB/etc/ssh"
echo "AuthorizedKeysFile $FB/data/ssh/authorized_keys .ssh/authorized_keys" > "$FB/etc/ssh/sshd_config"
d 'cat /usr/share/victor/authorized_keys' > "$FB/share/authorized_keys"
d 'cat /usr/share/victor/hub.env.default' > "$FB/share/hub.env.default"
echo 1 > "$FB/share/own-spine"
d 'cat /etc/victor-release' > "$FB/etc/victor-release"
VICTOR_DATA_WAIT=0 sh "$FB/firstboot" || fail "firstboot exit"
V="$FB/data/victor"
[[ "$(cat "$V/ssh.enabled")" == 1 ]] || fail "first boot: ssh.enabled != 1"
[[ -f "$V/ble.disabled" && -f "$V/anki.masked" && -f "$V/firstboot.done" ]] || fail "first boot flags"
grep -qx 'HUB_HOST=robot.mohammadabbasi.com' "$V/hub.env" || fail "hub.env"
grep -Eq '^192\.168\.0\.202 +robot\.mohammadabbasi\.com hub$' "$FB/etc/hosts" || fail "firstboot hosts"
[[ "$(cat "$FB/data/ssh/authorized_keys")" == "$K" ]] || fail "AuthorizedKeysFile on /data"
[[ "$(cat "$FB/home/root/.ssh/authorized_keys")" == "$K" ]] || fail "home authorized_keys"
# second boot: CHARGE-LATCH turned SSH off, operator restored Anki, someone put a key in hub.env
echo 0 > "$V/ssh.enabled"
rm -f "$V/anki.masked"
echo "OPENAI_API_KEY=$FAKE_KEY" >> "$V/hub.env"
VICTOR_DATA_WAIT=0 sh "$FB/firstboot" || fail "firstboot exit (2nd)"
[[ "$(cat "$V/ssh.enabled")" == 0 ]] || fail "latch state did not persist across reboot"
[[ ! -f "$V/anki.masked" ]] || fail "anki.masked re-created after restore-anki"
grep -q OPENAI "$V/hub.env" && fail "OpenAI key left in hub.env"
[[ "$(grep -c . "$FB/data/ssh/authorized_keys")" == 1 ]] || fail "authorized_keys duplicated"
pass "firstboot: SSH ON once, latch persists across reboot, hosts/hub.env/keys seeded, OpenAI stripped"

echo
echo "OTA packer test passed ($(wc -c < "$OTA") byte dummy .ota; nothing flashed)."
