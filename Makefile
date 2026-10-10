.PHONY: help test agent-arm ble-bootstrap hub prove prove1 prove2 prove-explore prove3 prove4 ota ota-check ota-serve ota-test ota-deps ota-robot ota-rollback flash flash-ssh verify release ota-all sync
.DEFAULT_GOAL := test

GO ?= go
AGENT := robot/agent
BOOT := deploy/ble-bootstrap

test: ## Go agent + hub unit tests
	cd $(AGENT) && $(GO) test ./...
	cd hub/app && python3 -m unittest test_phase3.py test_faces.py test_brain.py -v

agent-arm: ## Cross-compile victor-agent for the robot (ARMv7)
	mkdir -p $(AGENT)/dist
	cd $(AGENT) && CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 \
		$(GO) build -trimpath -ldflags='-s -w' -o dist/victor-agent ./cmd/victor-agent

ble-bootstrap: ## Build the BLE first-flash tool (needs libsodium-dev)
	cd $(BOOT) && $(GO) build -o ble-bootstrap .
# Needs libsodium headers+library (libsodium-dev). This host has no BLE radio;
# first-flash is for a machine that can see Vector-XXXX.

hub: ## Start the hub container
	docker compose -f hub/docker-compose.yml up -d --build

sync: agent-arm ## Install the agent on a running robot over SSH
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

prove4: ## Phase 4: recipes + offline OTA test
	./deploy/prove-phase4.sh

# ------------------------------------------------------------------ OTA
# Full steps: deploy/OTA.md. Typical use on your Linux machine:
#   make ota-deps                      # once
#   make release                       # check, build + rollback, flash, verify
# Everything is overridable: make ota-robot HUB_IP=192.168.0.50
# Secrets come from .env (never committed); the Wi-Fi password is never echoed.

envval = $(shell [ -f .env ] && sed -n 's/^$(1)=//p' .env | tail -n1)
lan_ip = $(shell ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($$i=="src"){print $$(i+1); exit}}')

HUB_IP   ?= $(or $(call envval,HUB_IP),$(lan_ip))
PIN      ?= $(call envval,VECTOR_BLE_PIN)
SSID     ?= $(call envval,WIFI_SSID)
PASSWORD ?=
OTA_ARGS ?=
OTA_FILE ?= dist/victor.ota
ROLLBACK_FILE ?= dist/rollback.ota
OTA_WORK ?= ota-work
DUMP_BOOT  ?= $(OTA_WORK)/boot.img
DUMP_SYSFS ?= $(OTA_WORK)/sysfs.robot.img
OTA_DEPS := e2fsprogs openssl gzip tar coreutils make golang-go openssh-client python3 libsodium-dev iproute2

ota-deps: ## Install build/flash packages (apt, asks for sudo), then preflight
	@if command -v apt-get >/dev/null 2>&1; then \
		echo "sudo apt-get install -y $(OTA_DEPS)"; \
		sudo apt-get update && sudo apt-get install -y $(OTA_DEPS); \
	elif command -v dnf >/dev/null 2>&1; then \
		echo "sudo dnf install -y e2fsprogs openssl gzip tar make golang openssh-clients python3 libsodium-devel iproute"; \
		sudo dnf install -y e2fsprogs openssl gzip tar make golang openssh-clients python3 libsodium-devel iproute; \
	else echo "no apt-get/dnf: install $(OTA_DEPS) yourself"; exit 1; fi
	@go version | awk '{ split($$3,v,"."); sub("go","",v[1]); if (v[1]<1 || (v[1]==1 && v[2]<22)) { print "Go " $$3 " is too old; need >= 1.22 (https://go.dev/dl/)"; exit 1 } }'
	@$(MAKE) --no-print-directory ota-check

ota-check: ## Preflight: are all tools for building the .ota installed?
	./deploy/make-ota.sh --check $(OTA_ARGS)

ota: ## Build dist/victor.ota with custom args: make ota OTA_ARGS="--boot b.img --sysfs s.ext4"
	./deploy/make-ota.sh $(OTA_ARGS)

ota-robot: ## Dump images + ota.pas from the robot, build dist/victor.ota AND dist/rollback.ota
	@test -n "$(HUB_IP)" || { echo "HUB_IP not set and LAN IP not detected: make ota-robot HUB_IP=<laptop-lan-ip>"; exit 2; }
	@echo "hub robot.mohammadabbasi.com -> $(HUB_IP)"
	./deploy/make-ota.sh --from-robot --hub-ip $(HUB_IP) --work $(OTA_WORK) --out $(OTA_FILE) $(OTA_ARGS)
	@$(MAKE) --no-print-directory ota-rollback

ota-rollback: ## Build dist/rollback.ota from the untouched robot dump (ota-work/)
	@test -s $(DUMP_BOOT) -a -s $(DUMP_SYSFS) || { echo "no dump in $(OTA_WORK)/ - run make ota-robot first"; exit 2; }
	./deploy/make-ota.sh --raw --boot $(DUMP_BOOT) --sysfs $(DUMP_SYSFS) --work $(OTA_WORK) --out $(ROLLBACK_FILE)
	@first=$(dir $(ROLLBACK_FILE))rollback-first.ota; if [ ! -f $$first ]; then cp $(ROLLBACK_FILE) $$first; echo "kept the first rollback as $$first (never overwritten)"; fi

ota-serve: ## Serve dist/victor.ota over plain HTTP on :8088 (manual ota-start)
	./deploy/serve-ota.sh $(OTA_FILE)

flash: export OTA_FILE := $(OTA_FILE)
flash: export ROLLBACK_FILE := $(ROLLBACK_FILE)
flash: export FLASH_PIN = $(PIN)
flash: export FLASH_WIFI_SSID = $(SSID)
flash: export FLASH_WIFI_PASSWORD = $(value PASSWORD)
flash: ## Flash via recovery BLE: make flash [PIN=..] [SSID=..] [PASSWORD=..] (asks to confirm)
	@./deploy/flash-ota.sh

flash-ssh: ## Flash over SSH from a running image (update-engine, inactive slot; no BLE)
	./deploy/flash-ssh.sh --reboot

verify: ## Check the robot booted our image (SSH, BLE masked, agent, camera, no OpenAI key)
	./deploy/verify-first-boot.sh

release: ## All-in-one: preflight, build + rollback, flash, verify
	@$(MAKE) --no-print-directory ota-check
	@$(MAKE) --no-print-directory ota-robot
	@$(MAKE) --no-print-directory flash
	@$(MAKE) --no-print-directory verify
ota-all: release ## Alias for release

ota-test: ## Offline end-to-end packer test (dummy images; no robot, no flash)
	./deploy/test-ota.sh

help: ## List targets
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z0-9_-]+:.*## /{printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
