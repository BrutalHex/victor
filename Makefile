.PHONY: test agent-arm ble-bootstrap hub prove sync

GO ?= go
AGENT := robot/agent
BOOT := deploy/ble-bootstrap

test:
	cd $(AGENT) && $(GO) test ./...

agent-arm:
	mkdir -p $(AGENT)/dist
	cd $(AGENT) && CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 \
		$(GO) build -trimpath -ldflags='-s -w' -o dist/victor-agent ./cmd/victor-agent

ble-bootstrap:
	cd $(BOOT) && $(GO) build -o ble-bootstrap .
# Needs libsodium headers+library (libsodium-dev). This host has no BLE radio;
# first-flash is for a machine that can see Vector-XXXX.

hub:
	docker compose -f hub/docker-compose.yml up -d --build

sync: agent-arm
	./deploy/sync-agent.sh

prove: test agent-arm
	./deploy/prove-phase0.sh

prove1: test agent-arm
	./deploy/prove-phase1.sh

prove2: test agent-arm
	./deploy/prove-phase2.sh
