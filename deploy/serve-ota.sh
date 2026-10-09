#!/usr/bin/env bash
# Serve a .ota over plain HTTP for recovery ota-start (recovery cannot pull HTTPS).
# usage: deploy/serve-ota.sh [dist/victor.ota] [port]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OTA="${1:-${OTA_FILE:-${ROOT}/dist/victor.ota}}"
PORT="${2:-${OTA_HTTP_PORT:-8088}}"

[[ -f "$OTA" ]] || { echo "serve-ota: $OTA not found; build it with: make ota OTA_ARGS='--from-robot'" >&2; exit 2; }
command -v python3 >/dev/null || { echo "serve-ota: need python3 (sudo apt install python3)" >&2; exit 2; }
if tar -tf "$OTA" 2>/dev/null | head -n1 | grep -qx manifest.ini; then :; else
  echo "serve-ota: $OTA is not an OTA tar with manifest.ini first" >&2; exit 2
fi
DIR="$(cd "$(dirname "$OTA")" && pwd)"
BASE="$(basename "$OTA")"

echo "Serving $DIR/$BASE on port $PORT (HTTP). The robot must reach one of:"
for ip in $(hostname -I 2>/dev/null || true); do
  case "$ip" in *:*|127.*|172.17.*) continue ;; esac
  echo "  http://${ip}:${PORT}/${BASE}"
done
echo "Pass that URL to:  ./deploy/first-flash ... --url <URL>"
echo "                or ./deploy/ble-bootstrap/ble-bootstrap ota-start --pin <PIN> --url <URL>"
echo "Open the port if a firewall is on (e.g. sudo ufw allow ${PORT}/tcp). Ctrl-C to stop."
exec python3 -m http.server "$PORT" --bind 0.0.0.0 --directory "$DIR"
