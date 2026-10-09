#!/usr/bin/env bash
# Build the HTTP .ota that recovery `ota-start` installs into the inactive
# A/B slot (boot + system only; SBL, ABOOT and recoveryfs are never touched).
#
# Vector OTA layout (what update-engine streams):
#   manifest.ini
#   apq8009-robot-boot.img.gz    gzip -9, then AES-256-CTR (openssl enc -md md5,
#   apq8009-robot-sysfs.img.gz   password file /anki/etc/ota.pas)
# manifest bytes/sha256 are of the *uncompressed* images.
#
# Inputs: a vendor boot image + an ext4 system image, either dumped from the
# robot (--from-robot) or produced by bitbake victor-image (--boot/--sysfs).
# Do not ship WireOS as the product: for the product use the bitbake
# victor-image sysfs. --from-robot repackages whatever runs on the robot now.
#
# The sysfs is copied, then overlaid (debugfs, no root, no loop mount) with
# everything GROK_INSTRUCTIONS.md needs on first boot:
#   /usr/bin/victor-agent              CHARGE-LATCH FSM, SSH latch + watchdog,
#                                      telemetry, BLE mask, /etc/hosts block
#   victor-agent / victor-firstboot / victor-ble-mask units, enabled
#   SSH unit enabled (sshd.socket or dropbear), never masked
#   ankibluetoothd / btproperty / vic-switchboard / bluetooth masked (/dev/null)
#   /usr/share/victor/authorized_keys  owner PUBLIC key (keys/*.pub) only
#   /usr/share/victor/hub.env.default  HUB_HOST=robot.mohammadabbasi.com
#   /etc/fstab /data -> rw,exec ; /etc/hosts hub block if --hub-ip
#   /etc/victor-release                version + git commit
# victor-firstboot (every boot, before ssh + agent) seeds /data/victor:
# ssh.enabled=1 and ble.disabled=1 only if missing (latch state persists),
# hub.env, anki.masked (agent owns the spine so CHARGE-LATCH works), and
# copies the public key into root's authorized_keys.
# The packer fails if the OpenAI key from .env (or any OPENAI_API_KEY=value /
# sk-proj- key) is found anywhere in the system image.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"
export PATH="${PATH}:/sbin:/usr/sbin"
export DEBUGFS_PAGER=__none__ PAGER=cat

OUT="${ROOT}/dist/victor.ota"
BOOT=""
SYSFS=""
FROM_ROBOT=0
VERSION="${OTA_VERSION:-3.0.1.victor}"
PAS="${OTA_PAS:-${ROOT}/keys/ota.pas}"
WORK="${OTA_WORK:-${ROOT}/ota-work}"
PUBKEY="${ROBOT_SSH_PUBKEY:-}"
BAKE_HUB_IP=""
HUB_HOST="${HUB_PUBLIC_NAME:-robot.mohammadabbasi.com}"
OWN_SPINE=1
AGENT_BIN=""
CHECK_ONLY=0
REBOOT_AFTER=0
SSH_UNIT_PREF="${OTA_SSH_UNIT:-}"
RAW=0

usage() {
  cat <<USAGE
usage: deploy/make-ota.sh (--from-robot | --boot boot.img --sysfs sysfs.img) [options]
       deploy/make-ota.sh --check [--from-robot]

inputs
  --from-robot          dump the robot's ACTIVE boot+system slot and
                        /anki/etc/ota.pas over SSH (\$ROBOT_SSH_IP, keys/ssh_root_key)
  --boot FILE           vendor APQ8009 boot image (kernel)  - not modified
  --sysfs FILE          ext4 system image (bitbake victor-image or a dump) - not modified
  --pass FILE           OTA password file [keys/ota.pas] (from robot /anki/etc/ota.pas)
options
  --out FILE            output [dist/victor.ota]
  --version V           update_version in manifest [${VERSION}]
  --pubkey FILE         SSH public key to authorize for root
                        [keys/ssh_root_key.pub, else derived from keys/ssh_root_key]
  --hub-ip IP           bake IP for ${HUB_HOST} into /etc/hosts + hub.env default
  --hub-host NAME       hub hostname [${HUB_HOST}]
  --no-own-spine        leave the body to stock Anki on first boot (CHARGE-LATCH
                        then cannot see the button; not recommended)
  --ssh-unit UNIT       force sshd.socket | dropbear.service | dropbear.socket
  --agent-bin FILE      use a prebuilt ARM victor-agent instead of 'make agent-arm'
  --reboot-after-install set manifest reboot_after_install=1 [0]
  --work DIR            scratch dir [ota-work/]
  --raw                 pack boot+sysfs UNCHANGED (rollback .ota from a pristine dump;
                        no overlay, no SSH/BLE changes)
  --check               only run the dependency preflight

Serve it over HTTP (recovery cannot pull HTTPS):  ./deploy/serve-ota.sh $OUT
USAGE
}

die() { echo "make-ota: ERROR: $*" >&2; exit 1; }
log() { echo "make-ota: $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from-robot) FROM_ROBOT=1; shift ;;
    --boot) BOOT="$2"; shift 2 ;;
    --sysfs) SYSFS="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --pass) PAS="$2"; shift 2 ;;
    --pubkey) PUBKEY="$2"; shift 2 ;;
    --hub-ip) BAKE_HUB_IP="$2"; shift 2 ;;
    --hub-host) HUB_HOST="$2"; shift 2 ;;
    --no-own-spine) OWN_SPINE=0; shift ;;
    --ssh-unit) SSH_UNIT_PREF="$2"; shift 2 ;;
    --agent-bin) AGENT_BIN="$2"; shift 2 ;;
    --reboot-after-install) REBOOT_AFTER=1; shift ;;
    --work) WORK="$2"; shift 2 ;;
    --check) CHECK_ONLY=1; shift ;;
    --raw) RAW=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown $1" >&2; usage; exit 2 ;;
  esac
done

# ---------------------------------------------------------------- preflight
hint() {
  case "$1" in
    debugfs|e2fsck|dumpe2fs) echo "e2fsprogs  (Debian/Ubuntu: sudo apt install e2fsprogs; Fedora: sudo dnf install e2fsprogs)" ;;
    openssl) echo "openssl    (sudo apt install openssl)" ;;
    gzip|tar|sha256sum|awk|sed|od|cmp) echo "coreutils/gzip/tar (sudo apt install coreutils gzip tar gawk sed)" ;;
    go) echo "Go >= 1.22 (https://go.dev/dl/ or sudo apt install golang-go), or pass --agent-bin" ;;
    make) echo "make       (sudo apt install make)" ;;
    ssh|scp|ssh-keygen) echo "OpenSSH client (sudo apt install openssh-client)" ;;
    *) echo "$1" ;;
  esac
}

preflight() {
  local missing=0 t
  local tools=(debugfs e2fsck dumpe2fs openssl gzip tar sha256sum awk sed od cmp)
  [[ -z "$AGENT_BIN" && "$RAW" == 0 ]] && tools+=(go make)
  [[ "$FROM_ROBOT" == 1 ]] && tools+=(ssh scp)
  for t in "${tools[@]}"; do
    if ! command -v "$t" >/dev/null 2>&1; then
      echo "  missing: $t -> install $(hint "$t")" >&2
      missing=1
    fi
  done
  if command -v openssl >/dev/null 2>&1; then
    if ! printf x | openssl enc -e -aes-256-ctr -md md5 -pass pass:x >/dev/null 2>&1; then
      echo "  openssl cannot do 'enc -aes-256-ctr -md md5' (need OpenSSL >= 1.0.2)" >&2
      missing=1
    fi
  fi
  if [[ "$FROM_ROBOT" == 1 && ! -f "$ROBOT_SSH_KEY" ]]; then
    echo "  missing SSH key $ROBOT_SSH_KEY (needed for --from-robot)" >&2
    missing=1
  fi
  if [[ "$missing" == 1 ]]; then
    die "preflight failed; install the tools above and re-run (deploy/make-ota.sh --check)"
  fi
  log "preflight ok ($(debugfs -V 2>&1 | head -n1); $(openssl version))"
}

preflight
if [[ "$CHECK_ONLY" == 1 ]]; then
  exit 0
fi

if [[ "$FROM_ROBOT" != 1 && ( -z "$BOOT" || -z "$SYSFS" ) ]]; then
  echo "need --from-robot or both --boot and --sysfs" >&2
  usage
  exit 2
fi

mkdir -p "$WORK" "$(dirname "$OUT")"
WORK="$(cd "$WORK" && pwd)"
STAGE="$(mktemp -d "${WORK}/stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

# ---------------------------------------------------------------- inputs
if [[ "$FROM_ROBOT" == 1 ]]; then
  log "reading active slot from ${ROBOT_SSH_IP}"
  SUFFIX="$(robot_ssh 'tr " " "\n" < /proc/cmdline | sed -n "s/^androidboot.slot_suffix=//p"' | tr -d '\r\n')"
  SUFFIX="${SUFFIX:-_a}"
  PARTDIR="$(robot_ssh 'for d in /dev/disk/by-partlabel /dev/block/bootdevice/by-name; do [ -e "$d/boot_a" ] && { echo "$d"; break; }; done' | tr -d '\r\n')"
  [[ -n "$PARTDIR" ]] || die "cannot find boot_a under /dev/disk/by-partlabel or /dev/block/bootdevice/by-name"
  log "active slot ${SUFFIX} (${PARTDIR})"
  log "copying /anki/etc/ota.pas -> ${PAS} (gitignored, never commit)"
  mkdir -p "$(dirname "$PAS")"
  robot_scp "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}:/anki/etc/ota.pas" "$PAS"
  chmod 600 "$PAS"
  log "dumping boot${SUFFIX}..."
  robot_ssh "dd if=${PARTDIR}/boot${SUFFIX} bs=1M 2>/dev/null" > "${WORK}/boot.img"
  log "dumping system${SUFFIX} (~900 MiB, a few minutes)..."
  robot_ssh "dd if=${PARTDIR}/system${SUFFIX} bs=4M 2>/dev/null" > "${WORK}/sysfs.robot.img"
  BOOT="${WORK}/boot.img"
  SYSFS="${WORK}/sysfs.robot.img"
  log "dumps kept; rebuild without the robot: --boot $BOOT --sysfs $SYSFS"
fi

[[ -s "$BOOT" ]] || die "boot image $BOOT missing or empty"
[[ -s "$SYSFS" ]] || die "sysfs image $SYSFS missing or empty"
[[ -s "$PAS" ]] || die "missing $PAS - copy it from the robot: scp root@\$ROBOT_SSH_IP:/anki/etc/ota.pas keys/ota.pas (or use --from-robot)"
case "$(head -c 8 "$BOOT" | od -An -c | tr -d ' ')" in
  ANDROID!*) ;;
  *) log "warning: $BOOT has no ANDROID! header; make sure it is the vendor boot image" ;;
esac

if [[ "$RAW" == 0 ]]; then
# SSH public key (never the private key).
if [[ -z "$PUBKEY" ]]; then
  if [[ -f "${ROOT}/keys/ssh_root_key.pub" ]]; then
    PUBKEY="${ROOT}/keys/ssh_root_key.pub"
  elif [[ -f "${ROBOT_SSH_KEY}.pub" ]]; then
    PUBKEY="${ROBOT_SSH_KEY}.pub"
  elif [[ -f "$ROBOT_SSH_KEY" ]] && command -v ssh-keygen >/dev/null 2>&1; then
    log "deriving public key from $ROBOT_SSH_KEY"
    ssh-keygen -y -f "$ROBOT_SSH_KEY" > "${STAGE}/derived.pub" || die "ssh-keygen -y failed"
    PUBKEY="${STAGE}/derived.pub"
  else
    die "no SSH public key: put it at keys/ssh_root_key.pub or pass --pubkey FILE"
  fi
fi
[[ -s "$PUBKEY" ]] || die "pubkey $PUBKEY missing or empty"
if grep -q 'PRIVATE KEY' "$PUBKEY"; then
  die "$PUBKEY is a PRIVATE key; pass the .pub file"
fi
grep -Eq '^(ssh-(rsa|ed25519|dss)|ecdsa-sha2-|sk-(ssh|ecdsa))' "$PUBKEY" || die "$PUBKEY does not look like an OpenSSH public key"

# victor-agent (static ARMv7).
if [[ -z "$AGENT_BIN" ]]; then
  log "building victor-agent (GOARCH=arm GOARM=7)"
  make -C "$ROOT" agent-arm >/dev/null
  AGENT_BIN="${ROOT}/robot/agent/dist/victor-agent"
fi
[[ -s "$AGENT_BIN" ]] || die "agent binary $AGENT_BIN missing"
# ELF e_machine 0x28 = ARM
[[ "$(od -An -tx1 -j18 -N2 "$AGENT_BIN" | tr -d ' \n')" == "2800" ]] || die "$AGENT_BIN is not an ARM ELF"
fi

# ---------------------------------------------------------------- sysfs copy
IMG="${WORK}/sysfs.victor.img"
log "copying $SYSFS -> $IMG (input is left untouched)"
cp --sparse=always "$SYSFS" "$IMG" 2>/dev/null || cp "$SYSFS" "$IMG"
if ! debugfs -R 'stats' "$IMG" >/dev/null 2>&1; then
  file "$SYSFS" 2>/dev/null || true
  die "debugfs cannot open $SYSFS as ext4 (need the raw system partition / bitbake .ext4)"
fi
set +e
if [[ "$RAW" == 1 ]]; then
  e2fsck -fn "$IMG" >"${STAGE}/fsck.pre" 2>&1 || log "warning: e2fsck -fn reports issues on the raw image (packed unchanged anyway)"
  rc=0
else
  e2fsck -fy "$IMG" >"${STAGE}/fsck.pre" 2>&1
  rc=$?
fi
set -e
(( rc < 4 )) || { cat "${STAGE}/fsck.pre" >&2; die "e2fsck on the input copy failed (rc=$rc)"; }

dfs() { debugfs -R "$1" "$IMG" 2>/dev/null; }
dfs_type() { dfs "stat $1" | awk '/Type:/{for(i=1;i<=NF;i++) if($i=="Type:"){print $(i+1); exit}}'; }
dfs_link() { dfs "stat $1" | sed -n 's/^Fast link dest: "\(.*\)"$/\1/p'; }

if [[ "$RAW" == 1 ]]; then
  log "--raw: packing images unchanged (rollback). No overlay."
else
# merged-/usr images have /lib -> usr/lib
UNITDIR=/lib/systemd/system
if [[ "$(dfs_type /lib)" != "directory" ]] || [[ "$(dfs_type /lib/systemd/system)" != "directory" && "$(dfs_type /usr/lib/systemd/system)" == "directory" ]]; then
  UNITDIR=/usr/lib/systemd/system
fi
ETCSYS=/etc/systemd/system
has_unit() { [[ -n "$(dfs_type "${UNITDIR}/$1")" || "$(dfs_type "${ETCSYS}/$1")" == "regular" ]]; }

# Same preference order as robot/agent/internal/sshctl (sshd.socket, then dropbear).
SSH_UNIT=""
if [[ -n "$SSH_UNIT_PREF" ]]; then
  has_unit "$SSH_UNIT_PREF" || die "--ssh-unit $SSH_UNIT_PREF not found in the sysfs"
  SSH_UNIT="$SSH_UNIT_PREF"
else
  for u in sshd.socket dropbear.service dropbear.socket; do
    if has_unit "$u"; then SSH_UNIT="$u"; break; fi
  done
fi
[[ -n "$SSH_UNIT" ]] || die "sysfs has no sshd.socket / dropbear.service / dropbear.socket; first boot would have no SSH. Build victor-image (ssh-server-dropbear) or use a sysfs with dropbear."
case "$SSH_UNIT" in
  *.socket) SSH_WANTS=sockets.target.wants ;;
  *) SSH_WANTS=multi-user.target.wants ;;
esac
log "unit dir ${UNITDIR}; SSH unit ${SSH_UNIT} (enabled, unmasked)"

# ---------------------------------------------------------------- overlay files
OV="${STAGE}/overlay"
mkdir -p "$OV"
Y="${ROOT}/yocto/meta-victor"
cp "${Y}/recipes-core/victor-agent/files/victor-agent.service" "${OV}/victor-agent.service"
cp "${Y}/recipes-core/victor-firstboot/files/victor-firstboot.service" "${OV}/victor-firstboot.service"
cp "${Y}/recipes-core/victor-firstboot/files/victor-firstboot.sh" "${OV}/victor-firstboot"
cp "${Y}/recipes-connectivity/victor-ble-mask/files/victor-ble-mask.service" "${OV}/victor-ble-mask.service"
cp "${Y}/recipes-connectivity/victor-ble-mask/files/victor-ble-mask.sh" "${OV}/victor-ble-mask"
cp "${ROOT}/robot/scripts/victor-set-hub-ip" "${OV}/victor-set-hub-ip"
grep -E '^(ssh-|ecdsa-|sk-ssh|sk-ecdsa)' "$PUBKEY" > "${OV}/authorized_keys"
{
  echo "# victor hub defaults (copied to /data/victor/hub.env on first boot)."
  echo "# OpenAI stays on the hub. Never put OPENAI_* here."
  echo "HUB_HOST=${HUB_HOST}"
  echo "HUB_GRPC_PORT=7443"
  [[ -n "$BAKE_HUB_IP" ]] && echo "HUB_IP=${BAKE_HUB_IP}"
} > "${OV}/hub.env.default"
echo 1 > "${OV}/own-spine"
GIT_REV="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
GIT_DIRTY="$(git -C "$ROOT" status --porcelain 2>/dev/null | grep -q . && echo -dirty || true)"
{
  echo "VICTOR_VERSION=${VERSION}"
  echo "VICTOR_GIT=${GIT_REV}${GIT_DIRTY}"
  echo "VICTOR_BUILT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "VICTOR_SSH_UNIT=${SSH_UNIT}"
} > "${OV}/victor-release"

# /etc/fstab: /data rw,exec
FSTAB_CHANGED=0
if [[ "$(dfs_type /etc/fstab)" == "regular" ]]; then
  dfs "cat /etc/fstab" > "${OV}/fstab.orig"
  awk '
    $2=="/data" && $1 !~ /^#/ {
      n=split($4, o, ","); out=""
      for (i=1;i<=n;i++) { if (o[i]=="noexec"||o[i]=="ro"||o[i]=="rw"||o[i]=="exec") continue; out=out "," o[i] }
      $4="rw,exec" out
    }
    { print }' "${OV}/fstab.orig" > "${OV}/fstab"
  cmp -s "${OV}/fstab.orig" "${OV}/fstab" || FSTAB_CHANGED=1
fi

# /etc/hosts: managed hub block
HOSTS_CHANGED=0
if [[ -n "$BAKE_HUB_IP" ]]; then
  if [[ "$(dfs_type /etc/hosts)" == "regular" ]]; then
    dfs "cat /etc/hosts" > "${OV}/hosts"
  else
    printf '127.0.0.1\tlocalhost\n' > "${OV}/hosts"
  fi
  sh "${OV}/victor-set-hub-ip" "$BAKE_HUB_IP" "$HUB_HOST" "${OV}/hosts"
  HOSTS_CHANGED=1
fi

# root's home on the rootfs (if it is a real directory there)
ROOT_HOME="$(dfs 'cat /etc/passwd' | awk -F: '$1=="root"{print $6; exit}')"
ROOT_HOME="${ROOT_HOME:-/home/root}"
HOME_ON_ROOTFS=0
case "$ROOT_HOME" in
  /data/*|/data) ;;
  *) [[ "$(dfs_type "$ROOT_HOME")" == "directory" ]] && HOME_ON_ROOTFS=1 ;;
esac

# ---------------------------------------------------------------- debugfs batch
CMDS="${STAGE}/debugfs.cmds"
: > "$CMDS"
q() { printf '%s\n' "$*" >> "$CMDS"; }
mkdir_p() {
  local p="" c
  local IFS=/
  for c in ${1#/}; do
    p="${p}/${c}"
    q "mkdir ${p}"
  done
}
own() { q "set_inode_field $1 uid 0"; q "set_inode_field $1 gid 0"; }
put() { # put SRC DEST MODE
  local src="$1" dst="$2" mode="$3" dir base
  dir="$(dirname "$dst")"; base="$(basename "$dst")"
  q "cd /"
  mkdir_p "$dir"
  q "rm ${dst}"
  q "cd ${dir}"
  q "write ${src} ${base}"
  q "cd /"
  q "set_inode_field ${dst} mode 0100${mode}"
  own "$dst"
}
link() { # link PATH TARGET
  q "cd /"
  mkdir_p "$(dirname "$1")"
  q "rm $1"
  q "symlink $1 $2"
}

put "$AGENT_BIN"                 /usr/bin/victor-agent 755
put "${OV}/victor-set-hub-ip"    /usr/bin/victor-set-hub-ip 755
put "${OV}/victor-firstboot"     /usr/bin/victor-firstboot 755
put "${OV}/victor-ble-mask"      /usr/bin/victor-ble-mask 755
put "${OV}/victor-agent.service"     "${UNITDIR}/victor-agent.service" 644
put "${OV}/victor-firstboot.service" "${UNITDIR}/victor-firstboot.service" 644
put "${OV}/victor-ble-mask.service"  "${UNITDIR}/victor-ble-mask.service" 644
put "${OV}/authorized_keys"      /usr/share/victor/authorized_keys 644
put "${OV}/hub.env.default"      /usr/share/victor/hub.env.default 644
put "${OV}/victor-release"       /etc/victor-release 644
if [[ "$OWN_SPINE" == 1 ]]; then
  put "${OV}/own-spine" /usr/share/victor/own-spine 644
else
  q "rm /usr/share/victor/own-spine"
fi
[[ "$FSTAB_CHANGED" == 1 ]] && put "${OV}/fstab" /etc/fstab 644
[[ "$HOSTS_CHANGED" == 1 ]] && put "${OV}/hosts" /etc/hosts 644
if [[ "$HOME_ON_ROOTFS" == 1 ]]; then
  q "cd /"
  mkdir_p "${ROOT_HOME}/.ssh"
  q "set_inode_field ${ROOT_HOME}/.ssh mode 040700"
  own "${ROOT_HOME}/.ssh"
  # keep any keys already authorized in the image, add ours
  if [[ "$(dfs_type "${ROOT_HOME}/.ssh/authorized_keys")" == "regular" ]]; then
    dfs "cat ${ROOT_HOME}/.ssh/authorized_keys" > "${OV}/root_authorized_keys"
  else
    : > "${OV}/root_authorized_keys"
  fi
  while IFS= read -r k; do
    grep -qxF "$k" "${OV}/root_authorized_keys" || echo "$k" >> "${OV}/root_authorized_keys"
  done < "${OV}/authorized_keys"
  put "${OV}/root_authorized_keys" "${ROOT_HOME}/.ssh/authorized_keys" 600
fi

# A stale /etc override from deploy/sync-agent.sh would shadow our unit.
q "rm ${ETCSYS}/victor-agent.service"

# enable our units
for u in victor-firstboot.service victor-ble-mask.service victor-agent.service; do
  link "${ETCSYS}/multi-user.target.wants/${u}" "${UNITDIR}/${u}"
done
# SSH: unmask + enable
q "rm ${ETCSYS}/${SSH_UNIT}"
link "${ETCSYS}/${SSH_WANTS}/${SSH_UNIT}" "${UNITDIR}/${SSH_UNIT}"
# BLE: masked on the running image (recoveryfs is a different partition and keeps BLE)
BLE_UNITS=(ankibluetoothd.service btproperty.service vic-switchboard.service bluetooth.service)
for u in "${BLE_UNITS[@]}"; do
  link "${ETCSYS}/${u}" /dev/null
done

# free space check (rough: sum of files + 1 MiB)
NEED=$(( $(cat "$AGENT_BIN" "${OV}"/* 2>/dev/null | wc -c) + 1048576 ))
read -r FREE_BLOCKS BLOCK_SIZE < <(dumpe2fs -h "$IMG" 2>/dev/null | awk -F: '/^Free blocks/{f=$2} /^Block size/{b=$2} END{gsub(/ /,"",f); gsub(/ /,"",b); print f, b}')
if (( FREE_BLOCKS * BLOCK_SIZE < NEED )); then
  die "sysfs has $(( FREE_BLOCKS * BLOCK_SIZE )) bytes free, overlay needs ~${NEED}. Use a larger system image."
fi

log "overlaying $(grep -c . "$CMDS") debugfs ops"
debugfs -w -f "$CMDS" "$IMG" > "${STAGE}/debugfs.log" 2>&1 || { cat "${STAGE}/debugfs.log" >&2; die "debugfs batch failed"; }

# ---------------------------------------------------------------- verify sysfs
VERIFY_FAIL=0
vfail() { echo "  verify: $*" >&2; VERIFY_FAIL=1; }
want_file() { # path mode
  local st
  st="$(dfs "stat $1")"
  [[ "$st" == *"Type: regular"* ]] || { vfail "$1 missing"; return; }
  [[ "$st" =~ Mode:\ +0?$2 ]] || vfail "$1 mode != $2"
  [[ "$st" =~ User:\ +0\ +Group:\ +0 ]] || vfail "$1 not owned by root"
}
want_link() { [[ "$(dfs_link "$1")" == "$2" ]] || vfail "$1 is not a link to $2"; }
want_file /usr/bin/victor-agent 755
want_file /usr/bin/victor-set-hub-ip 755
want_file /usr/bin/victor-firstboot 755
want_file /usr/bin/victor-ble-mask 755
for u in victor-agent victor-firstboot victor-ble-mask; do
  want_file "${UNITDIR}/${u}.service" 644
  want_link "${ETCSYS}/multi-user.target.wants/${u}.service" "${UNITDIR}/${u}.service"
done
want_file /usr/share/victor/authorized_keys 644
want_file /usr/share/victor/hub.env.default 644
want_file /etc/victor-release 644
want_link "${ETCSYS}/${SSH_WANTS}/${SSH_UNIT}" "${UNITDIR}/${SSH_UNIT}"
[[ "$(dfs_link "${ETCSYS}/${SSH_UNIT}")" == "/dev/null" ]] && vfail "${SSH_UNIT} is masked"
for u in "${BLE_UNITS[@]}"; do want_link "${ETCSYS}/${u}" /dev/null; done
# Image recognition runs on the hub; the robot must keep its camera stack.
CAMERA_UNITS="$(dfs "ls -p ${UNITDIR}" | awk -F/ '$6 ~ /camera/ {print $6}')"
for u in $CAMERA_UNITS; do
  [[ "$(dfs_link "${ETCSYS}/${u}")" != "/dev/null" ]] || vfail "camera unit $u is masked"
done
[[ -z "$(dfs_type "${ETCSYS}/victor-agent.service")" ]] || vfail "stale ${ETCSYS}/victor-agent.service still present"
cmp -s "$AGENT_BIN" <(dfs "cat /usr/bin/victor-agent") || vfail "victor-agent content mismatch"
cmp -s "${OV}/authorized_keys" <(dfs "cat /usr/share/victor/authorized_keys") || vfail "authorized_keys mismatch"
if [[ "$HOME_ON_ROOTFS" == 1 ]]; then want_file "${ROOT_HOME}/.ssh/authorized_keys" 600; fi
[[ "$VERIFY_FAIL" == 0 ]] || die "overlay verification failed (debugfs log: see above)"

set +e
e2fsck -fn "$IMG" > "${STAGE}/fsck.post" 2>&1
rc=$?
set -e
if (( rc != 0 )); then
  e2fsck -fy "$IMG" >/dev/null 2>&1 || true
  e2fsck -fn "$IMG" >/dev/null 2>&1 || { cat "${STAGE}/fsck.post" >&2; die "sysfs not clean after overlay"; }
fi

fi # RAW

# ---------------------------------------------------------------- no OpenAI key
if [[ -n "${OPENAI_API_KEY:-}" ]] && LC_ALL=C grep -aqF -- "$OPENAI_API_KEY" "$IMG"; then
  die "the OPENAI_API_KEY from .env is inside the system image. OpenAI stays on the hub."
fi
if LC_ALL=C grep -aqE 'OPENAI_API_KEY=[A-Za-z0-9_-]{8,}|sk-proj-[A-Za-z0-9_-]{20,}' "$IMG"; then
  die "an OpenAI-looking key is inside the system image. OpenAI stays on the hub."
fi
if [[ "$RAW" == 0 ]]; then
  log "verified: agent, units, SSH (${SSH_UNIT}) on, BLE masked, camera units untouched (${CAMERA_UNITS:-none found}), pubkey, no OpenAI key"
fi

# ---------------------------------------------------------------- pack
hash_img() { echo "$(wc -c < "$1" | tr -d ' ') $(sha256sum "$1" | awk '{print $1}')"; }
encrypt_gz() {
  gzip -c -n -9 "$1" | openssl enc -e -aes-256-ctr -md md5 -pass file:"$PAS" -out "$2" 2>"${STAGE}/openssl.err" \
    || { cat "${STAGE}/openssl.err" >&2; die "openssl encrypt failed"; }
}
decrypt_sha() {
  openssl enc -d -aes-256-ctr -md md5 -pass file:"$PAS" -in "$1" 2>/dev/null | gzip -dc | sha256sum | awk '{print $1}'
}

read -r BOOT_BYTES BOOT_SHA <<<"$(hash_img "$BOOT")"
read -r SYS_BYTES SYS_SHA <<<"$(hash_img "$IMG")"
log "boot  bytes=$BOOT_BYTES sha256=$BOOT_SHA"
log "sysfs bytes=$SYS_BYTES sha256=$SYS_SHA"

PK="${STAGE}/pack"
mkdir -p "$PK"
log "compressing + encrypting (this takes a while for a full sysfs)"
encrypt_gz "$BOOT" "${PK}/apq8009-robot-boot.img.gz"
encrypt_gz "$IMG" "${PK}/apq8009-robot-sysfs.img.gz"

[[ "$(decrypt_sha "${PK}/apq8009-robot-boot.img.gz")" == "$BOOT_SHA" ]] || die "boot round-trip decrypt mismatch"
[[ "$(decrypt_sha "${PK}/apq8009-robot-sysfs.img.gz")" == "$SYS_SHA" ]] || die "sysfs round-trip decrypt mismatch"

cat > "${PK}/manifest.ini" <<MANIFEST
[META]
manifest_version=1.0.0
update_version=${VERSION}
ankidev=1
num_images=2
reboot_after_install=${REBOOT_AFTER}
[BOOT]
encryption=1
delta=0
compression=gz
wbits=31
bytes=${BOOT_BYTES}
sha256=${BOOT_SHA}
[SYSTEM]
encryption=1
delta=0
compression=gz
wbits=31
bytes=${SYS_BYTES}
sha256=${SYS_SHA}
MANIFEST

# manifest.ini first: update-engine streams the tar.
tar -C "$PK" --format=ustar --owner=0 --group=0 --numeric-owner -cf "$OUT" \
  manifest.ini apq8009-robot-boot.img.gz apq8009-robot-sysfs.img.gz
sha256sum "$OUT" | awk '{print $1}' > "${OUT}.sha256"
log "wrote $OUT ($(wc -c < "$OUT") bytes, sha256 $(cat "${OUT}.sha256"))"
cat <<NEXT

Next (see deploy/OTA.md):
  make flash                 # needs dist/rollback.ota too (make ota-rollback); asks before flashing
or serve it yourself (HTTP, not HTTPS) and run ota-start from recovery:
  ./deploy/serve-ota.sh $OUT
NEXT
