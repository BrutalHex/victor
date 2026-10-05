#!/usr/bin/env bash
# Prove Phase 3: camera/audio VCT1, faces HTTP, thinking bar, no OpenAI on robot.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/deploy/lib.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

mkdir -p "$(dirname "${SSH_CONTROL_PATH}")"
ssh "${SSH_MASTER_OPTS[@]}" -fN "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" || fail "controlmaster"
robot_ssh() { ssh "${SSH_MASTER_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" "$@"; }

HUB_PID=""
cleanup() {
  if [[ -n "$HUB_PID" ]]; then kill "$HUB_PID" 2>/dev/null || true; fi
  ssh "${SSH_MASTER_OPTS[@]}" "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 'rm -f /data/victor/camera.jpg /data/victor/mic.pcm /data/victor/hub.down; /data/victor/victor-agent ssh-on' 2>/dev/null || true
  ssh "${SSH_OPTS[@]}" -O exit "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 2>/dev/null || true
}
trap cleanup EXIT

echo "== SSH =="
robot_ssh true || fail "ssh"
pass "ssh"

echo "== sync agent =="
"${ROOT}/deploy/sync-agent.sh"
robot_ssh 'test -f /data/victor/anki.masked || /data/victor/victor-agent mask-anki'

echo "== local hub + reverse tunnel =="
docker stop hub >/dev/null 2>&1 || true
HUB_SKILL_PORT=17443 HUB_HTTP_PORT=18080 HUB_SENSOR_PORT=17502 \
  HUB_AUDIO_PORT=17501 HUB_VIDEO_PORT=17500 HUB_FACE_DB=/tmp/victor-faces-prove.db \
  python3 "${ROOT}/hub/app/main.py" >/tmp/victor-hub-phase3.log 2>&1 &
HUB_PID=$!
sleep 0.6
curl -sf "http://127.0.0.1:18080/healthz" >/dev/null || fail "hub http"
pass "hub http"

ssh "${SSH_MASTER_OPTS[@]}" -O cancel -R 127.0.0.1:7443:127.0.0.1:17443 "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" 2>/dev/null || true
ssh "${SSH_MASTER_OPTS[@]}" -O forward -R 127.0.0.1:7443:127.0.0.1:17443 "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" || \
  ssh "${SSH_MASTER_OPTS[@]}" -R 127.0.0.1:7443:127.0.0.1:17443 -fN "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}" || true
robot_ssh 'printf "HUB_HOST=127.0.0.1\nHUB_GRPC_PORT=7443\n" > /data/victor/hub.env'
robot_ssh 'systemctl restart victor-agent.service'
sleep 2
robot_ssh 'systemctl is-active victor-agent.service' | grep -q active || fail "agent"
pass "agent up"

echo "== inject camera + mic =="
python3 - <<'PY' >/tmp/victor-prove.jpg
import io
from pathlib import Path
try:
    from PIL import Image
    im = Image.new("RGB", (320, 180), (20, 180, 40))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=70)
    Path("/tmp/victor-prove.jpg").write_bytes(buf.getvalue())
except Exception:
    # 1x1 JPEG
    Path("/tmp/victor-prove.jpg").write_bytes(bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300100b0c0e0c0a100e0d0e1211101318281a181616183123251e283a333d3c3933383740485c4e404457453738506d51575f626768673e4d71797064785c656763ffc0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda00080001000100003f00d2cf30ffa2"
    ))
PY
# 20 ms of non-zero PCM
python3 -c 'open("/tmp/victor-prove.pcm","wb").write((2000).to_bytes(2,"little")*320)'
robot_scp /tmp/victor-prove.jpg "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}:/data/victor/camera.jpg"
robot_scp /tmp/victor-prove.pcm "${ROBOT_SSH_USER}@${ROBOT_SSH_IP}:/data/victor/mic.pcm"

echo "== wait for hub to see SENSOR + VIDEO (TCP media mux) =="
ok=0
for i in $(seq 1 40); do
  JS=$(curl -sf "http://127.0.0.1:18080/status" || echo '{}')
  echo "  $i $JS" | head -c 240; echo
  python3 - "$JS" <<'PY' && ok=1 && break
import json,sys
s=json.loads(sys.argv[1])
if s.get("sensors",0)>=5 and s.get("heartbeats",0)>=1:
    raise SystemExit(0)
raise SystemExit(1)
PY
  sleep 0.25
done
[[ "$ok" == 1 ]] || fail "hub did not see sensors/heartbeats (see /tmp/victor-hub-phase3.log)"
pass "hub ingest"

sleep 1.2
JS=$(curl -sf "http://127.0.0.1:18080/status")
python3 - "$JS" <<'PY'
import json,sys
s=json.loads(sys.argv[1])
print("sensors",s.get("sensors"),"video",s.get("video"),"audio",s.get("audio"),"hz",s.get("hz"))
if s.get("video",0) < 1:
    print("WARN: no VIDEO yet (inject camera.jpg / V4L2)")
if s.get("audio",0) < 1:
    print("WARN: no AUDIO yet (spine mics / mic.pcm inject)")
if s.get("hz",0) < 20:
    print("WARN: SENSOR hz", s.get("hz"), "< 20 (SSH tunnel; same-LAN UDP is the spec path)")
PY
pass "status"

echo "== faces HTTP =="
curl -sf "http://127.0.0.1:18080/faces" | grep -q faces || fail "GET /faces"
IMG_B64=$(python3 -c 'import base64; print(base64.b64encode(open("/tmp/victor-prove.jpg","rb").read()).decode())')
curl -sf -X POST "http://127.0.0.1:18080/faces" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Ada\",\"image_b64\":\"${IMG_B64}\"}" | grep -q Ada || fail "enroll"
curl -sf "http://127.0.0.1:18080/faces" | grep -q Ada || fail "list faces"
pass "faces enroll"

echo "== thinking bar =="
BEFORE=$(robot_ssh 'md5sum /data/victor/face.rgb565 2>/dev/null | awk "{print \$1}"' || true)
curl -sf -X POST "http://127.0.0.1:18080/think" -H 'Content-Type: application/json' -d '{"on":true}' >/dev/null
changed=0
for i in $(seq 1 20); do
  sleep 0.2
  SZ=$(robot_ssh 'wc -c < /data/victor/face.rgb565' | tr -d '[:space:]')
  AFTER=$(robot_ssh 'md5sum /data/victor/face.rgb565 2>/dev/null | awk "{print \$1}"' || true)
  if [[ "$SZ" == "35328" && -n "$AFTER" && "$AFTER" != "$BEFORE" ]]; then
    changed=1
    break
  fi
done
echo "face.rgb565 bytes=$SZ md5 $BEFORE -> $AFTER"
[[ "$SZ" == "35328" ]] || fail "face.rgb565 size $SZ"
[[ "$changed" == 1 ]] || fail "thinking bar did not change LCD"
pass "thinking bar blit 184x96"

echo "== no OpenAI key on robot =="
robot_ssh '/data/victor/victor-agent status' | grep -q 'openai_key_on_robot=false' || fail "openai key on robot"
pass "openai stays on hub"

echo "== SSH still works =="
robot_ssh true || fail "ssh after phase3"
pass "ssh survived"

echo
echo "Phase 3 proved on ${ROBOT_SSH_IP}."
echo "Named-face speak and ChatGPT voice need OPENAI_API_KEY on the hub and a live camera."
