.PHONY: test agent-arm ble-bootstrap hub prove prove1 prove2 prove-explore prove3 prove4 ota sync

GO ?= go
AGENT := robot/agent
BOOT := deploy/ble-bootstrap

test:
	cd $(AGENT) && $(GO) test ./...
	cd hub/app && python3 -m unittest test_phase3.py -v

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

# On-charger gate only. Off-charger creep: ./deploy/prove-explore.sh --drive
prove-explore: test agent-arm
	./deploy/prove-explore.sh

prove3: test agent-arm
	./deploy/prove-phase3.sh

prove4:
	./deploy/prove-phase4.sh

ota: agent-arm
	./deploy/make-ota.sh
