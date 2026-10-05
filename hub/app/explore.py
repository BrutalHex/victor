"""Desk wander. Skills only — never PWM. Classical cliff veto on the robot still wins."""

from __future__ import annotations

import time

IDLE = 0
STOP = 1
BACK_OFF = 2
TURN_LEFT = 3
TURN_RIGHT = 4
CREEP_FORWARD = 5
LOOK_UP = 6
LOOK_DOWN = 7
DOCK = 8

NAMES = {
    IDLE: "idle",
    STOP: "stop",
    BACK_OFF: "back_off",
    TURN_LEFT: "turn_left",
    TURN_RIGHT: "turn_right",
    CREEP_FORWARD: "creep_forward",
    LOOK_UP: "look_up",
    LOOK_DOWN: "look_down",
    DOCK: "dock",
}


class Explorer:
    def __init__(self) -> None:
        self.state = IDLE
        self.entered = time.time()
        self.turn_left = True

    def step(self, sensor: dict | None, veto: int, edge_vote: int = 0) -> int:
        now = time.time()
        if sensor is None:
            self.state = IDLE
            return IDLE
        if veto:
            self.state = STOP
            self.entered = now
            return STOP
        # Hub edge model may vote stop/back_off. IR cliffs on the robot still win.
        if edge_vote in (STOP, BACK_OFF):
            self.state = edge_vote
            self.entered = now
            return edge_vote
        if sensor.get("on_charger"):
            # Wheels stay 0 on the contacts. Nod the head so the body is visibly alive.
            dt = now - self.entered
            if self.state not in (LOOK_UP, LOOK_DOWN) or dt > 1.2:
                self.state = LOOK_DOWN if self.state == LOOK_UP else LOOK_UP
                self.entered = now
            return self.state
        cliffs = sensor.get("cliffs") or (0, 0, 0, 0)
        if cliffs[0] < 80 or cliffs[1] < 80:
            self.state = BACK_OFF
            self.entered = now
            return BACK_OFF

        dt = now - self.entered
        if self.state in (IDLE, STOP, DOCK, BACK_OFF) and dt > 0.3:
            self.state = CREEP_FORWARD
            self.entered = now
            return CREEP_FORWARD
        if self.state == CREEP_FORWARD and dt > 3.5:
            self.state = LOOK_UP if int(now) % 2 == 0 else LOOK_DOWN
            self.entered = now
            return self.state
        if self.state in (LOOK_UP, LOOK_DOWN) and dt > 0.35:
            self.state = TURN_LEFT if self.turn_left else TURN_RIGHT
            self.turn_left = not self.turn_left
            self.entered = now
            return self.state
        if self.state in (TURN_LEFT, TURN_RIGHT) and dt > 0.45:
            self.state = CREEP_FORWARD
            self.entered = now
            return CREEP_FORWARD
        return self.state
