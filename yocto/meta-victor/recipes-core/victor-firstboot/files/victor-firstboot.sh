#!/bin/sh
# Runs every boot, before dropbear/sshd and victor-agent.
# Shared by the Yocto image (victor-firstboot recipe) and deploy/make-ota.sh.
#
# First boot of our OTA: SSH ON so ble-bootstrap can finish, BLE masked,
# /data rw,exec, hub hostname robot.mohammadabbasi.com. After that the files
# in /data/victor are the source of truth (CHARGE-LATCH owns ssh.enabled), so
# nothing here overwrites an existing flag.
# No OpenAI key ever lives on the robot.
D=/data/victor
SHARE=/usr/share/victor

# Wait for /data (Vector: mount-data.service, dm-crypt userdata) so nothing
# is written to the read-only rootfs underneath it.
i=0
while [ "$i" -lt "${VICTOR_DATA_WAIT:-60}" ] && ! awk '$2=="/data"{f=1} END{exit !f}' /proc/mounts; do
  sleep 1
  i=$((i + 1))
done
# /data must be writable and executable.
mount -o remount,rw,exec /data 2>/dev/null || true
mkdir -p "$D"

# SSH ON on first boot; latch state persists afterwards.
[ -f "$D/ssh.enabled" ] || echo 1 > "$D/ssh.enabled"
# Running image never advertises BLE (recoveryfs keeps it for unbrick).
[ -f "$D/ble.disabled" ] || echo 1 > "$D/ble.disabled"

# Hub hostname defaults. Never overwrite an operator hub.env.
if [ ! -f "$D/hub.env" ]; then
  if [ -f "$SHARE/hub.env.default" ]; then
    cp "$SHARE/hub.env.default" "$D/hub.env"
  else
    printf 'HUB_HOST=robot.mohammadabbasi.com\nHUB_GRPC_PORT=7443\n' > "$D/hub.env"
  fi
fi
# OpenAI stays on the hub.
if grep -q 'OPENAI' "$D/hub.env" 2>/dev/null; then
  sed -i '/OPENAI/d' "$D/hub.env"
fi
HUB_IP=$(sed -n 's/^HUB_IP=//p' "$D/hub.env" | tail -n 1)
HUB_HOST=$(sed -n 's/^HUB_HOST=//p' "$D/hub.env" | tail -n 1)
if [ -n "$HUB_IP" ] && [ -x /usr/bin/victor-set-hub-ip ]; then
  /usr/bin/victor-set-hub-ip "$HUB_IP" "${HUB_HOST:-robot.mohammadabbasi.com}" 2>/dev/null || true
fi

# victor-agent owns the spine (needed for CHARGE-LATCH) on the first boot of
# this image. deploy/restore-anki.sh removes the flag and it stays removed.
if [ -f "$SHARE/own-spine" ] && [ ! -f "$D/firstboot.done" ]; then
  echo 1 > "$D/anki.masked"
fi

# Owner's SSH public key. Covers homes and AuthorizedKeysFile paths on /data
# that the OTA packer cannot write at build time.
if [ -s "$SHARE/authorized_keys" ]; then
  ROOT_HOME=$(awk -F: '$1=="root"{print $6}' /etc/passwd 2>/dev/null)
  TARGETS="${ROOT_HOME:-/home/root}/.ssh/authorized_keys"
  if [ -f /etc/ssh/sshd_config ]; then
    for f in $(sed -n 's/^[[:space:]]*AuthorizedKeysFile[[:space:]]\{1,\}//p' /etc/ssh/sshd_config); do
      f=$(echo "$f" | sed "s#%h#${ROOT_HOME:-/home/root}#g; s#%u#root#g; s#%%#%#g")
      case "$f" in /*) ;; *) f="${ROOT_HOME:-/home/root}/$f" ;; esac
      TARGETS="$TARGETS $f"
    done
  fi
  for t in $TARGETS; do
    dir=$(dirname "$t")
    mkdir -p "$dir" 2>/dev/null || continue
    touch "$t" 2>/dev/null || continue
    while IFS= read -r k; do
      [ -n "$k" ] || continue
      grep -qxF "$k" "$t" 2>/dev/null || echo "$k" >> "$t"
    done < "$SHARE/authorized_keys"
    chown root:root "$dir" "$t" 2>/dev/null || true
    chmod 700 "$dir" 2>/dev/null || true
    chmod 600 "$t" 2>/dev/null || true
  done
fi

[ -f "$D/firstboot.done" ] || cat /etc/victor-release > "$D/firstboot.done" 2>/dev/null || date > "$D/firstboot.done"
exit 0
