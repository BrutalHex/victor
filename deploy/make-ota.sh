#!/usr/bin/env bash
# Pack a WireOS/dev-compatible HTTP .ota (recovery ota-start).
#
# Real Vector OTAs are a tar of:
#   manifest.ini
#   apq8009-robot-boot.img.gz   (gzip then AES-256-CTR with /anki/etc/ota.pas)
#   apq8009-robot-sysfs.img.gz
# manifest sha256/bytes are of the *uncompressed* image. WireOS 3.x does not
# ship manifest.sha256 (Anki prod OTAs did; we are on anki.dev).
#
# Do not ship WireOS as the product. This packs *this robot's* vendor kernel
# + current sysfs with victor-agent overlaid.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

OUT="${ROOT}/dist/victor.ota"
BOOT=""
SYSFS=""
FROM_ROBOT=0
VERSION="${OTA_VERSION:-3.0.1.victor}"
PAS="${ROOT}/keys/ota.pas"
WORK="${ROOT}/ota-work"

usage() {
  cat <<EOF
usage: deploy/make-ota.sh [--from-robot] [--boot boot.img] [--sysfs sysfs.img] [--out path]

--from-robot  dd boot_a + system_a over SSH, overlay victor-agent, encrypt, tar
HTTP URL for recovery: http://<dev-ip>:8088/victor.ota  (not HTTPS)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from-robot) FROM_ROBOT=1; shift ;;
    --boot) BOOT="$2"; shift 2 ;;
    --sysfs) SYSFS="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --pass) PAS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown $1"; usage; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$OUT")" "$WORK"
make -C "$ROOT" agent-arm >/dev/null

if [[ "$FROM_ROBOT" == 1 ]]; then
  echo "copying ota.pas from robot (not committed)"
  robot_scp "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}:/anki/etc/ota.pas" "$PAS"
  chmod 600 "$PAS"
  echo "dumping boot_a (32 MiB)..."
  robot_ssh 'dd if=/dev/disk/by-partlabel/boot_a bs=1M' > "${WORK}/boot.img"
  echo "dumping system_a (~900 MiB, this takes a few minutes)..."
  robot_ssh 'dd if=/dev/disk/by-partlabel/system_a bs=4M' > "${WORK}/sysfs.img"
  BOOT="${WORK}/boot.img"
  SYSFS="${WORK}/sysfs.img"
fi

if [[ -z "$BOOT" || -z "$SYSFS" ]]; then
  echo "need --from-robot or both --boot and --sysfs" >&2
  usage
  exit 2
fi
if [[ ! -f "$PAS" ]]; then
  echo "missing $PAS — scp from robot /anki/etc/ota.pas" >&2
  exit 2
fi

echo "overlaying victor-agent into sysfs via debugfs"
# ext4 magic is at offset 0x438. Some dumps are raw partitions.
if ! debugfs -R 'pwd' "$SYSFS" >/dev/null 2>&1; then
  echo "debugfs cannot open $SYSFS" >&2
  file "$SYSFS"
  exit 1
fi
UNIT="${WORK}/victor-agent.ota.service"
cat > "$UNIT" <<'EOF'
[Unit]
Description=victor-agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/victor-agent run
Restart=always
RestartSec=2
EnvironmentFile=-/data/victor/hub.env
WorkingDirectory=/data/victor

[Install]
WantedBy=multi-user.target
EOF
debugfs -w -R 'mkdir /usr/bin' "$SYSFS" >/dev/null 2>&1 || true
debugfs -w -R 'rm /usr/bin/victor-agent' "$SYSFS" >/dev/null 2>&1 || true
debugfs -w -R "write ${ROOT}/robot/agent/dist/victor-agent /usr/bin/victor-agent" "$SYSFS"
debugfs -w -R 'chmod 755 /usr/bin/victor-agent' "$SYSFS" >/dev/null 2>&1 || true
debugfs -w -R 'mkdir /lib/systemd' "$SYSFS" >/dev/null 2>&1 || true
debugfs -w -R 'mkdir /lib/systemd/system' "$SYSFS" >/dev/null 2>&1 || true
debugfs -w -R 'rm /lib/systemd/system/victor-agent.service' "$SYSFS" >/dev/null 2>&1 || true
debugfs -w -R "write ${UNIT} /lib/systemd/system/victor-agent.service" "$SYSFS"
debugfs -w -R "write ${ROOT}/robot/scripts/victor-set-hub-ip /usr/bin/victor-set-hub-ip" "$SYSFS" >/dev/null 2>&1 || true
debugfs -R 'stat /usr/bin/victor-agent' "$SYSFS" | head -n 8

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

hash_img() {
  local img="$1"
  local bytes sha
  bytes="$(wc -c < "$img" | tr -d ' ')"
  sha="$(sha256sum "$img" | awk '{print $1}')"
  echo "$bytes $sha"
}

encrypt_gz() {
  local img="$1" dest="$2"
  gzip -c -n -9 "$img" > "${dest}.plain.gz"
  openssl enc -e -aes-256-ctr -md md5 -pass file:"$PAS" -in "${dest}.plain.gz" -out "$dest"
  rm -f "${dest}.plain.gz"
}

read -r BOOT_BYTES BOOT_SHA <<<"$(hash_img "$BOOT")"
read -r SYS_BYTES SYS_SHA <<<"$(hash_img "$SYSFS")"
echo "boot  bytes=$BOOT_BYTES sha256=$BOOT_SHA"
echo "sysfs bytes=$SYS_BYTES sha256=$SYS_SHA"

encrypt_gz "$BOOT" "${STAGE}/apq8009-robot-boot.img.gz"
encrypt_gz "$SYSFS" "${STAGE}/apq8009-robot-sysfs.img.gz"

cat > "${STAGE}/manifest.ini" <<EOF
[META]
manifest_version=1.0.0
update_version=${VERSION}
ankidev=1
num_images=2
reboot_after_install=0
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
EOF

tar -C "$STAGE" -cf "$OUT" manifest.ini apq8009-robot-boot.img.gz apq8009-robot-sysfs.img.gz
echo "wrote $OUT ($(wc -c < "$OUT") bytes)"
echo "flash: put Vector in recovery, then:"
echo "  ./deploy/first-flash --pin … --ssid … --password … --ota-file $OUT"
echo "or on a running unlocked image:"
echo "  UPDATE_ENGINE_URL=http://<this-host>:8088/$(basename "$OUT") /anki/bin/update-engine"
