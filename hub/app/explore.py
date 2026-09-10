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

    def step(self, sensor: dict | None, veto: int) -> int:
        now = time.time()
        if sensor is None:
            self.state = IDLE
            return IDLE
        if veto:
            self.state = STOP
            self.entered = now
            return STOP
        if sensor.get("on_charger"):
            self.state = DOCK
            self.entered = now
            return DOCK
        cliffs = sensor.get("cliffs") or (0, 0, 0, 0)
        if cliffs[0] < 80 or cliffs[1] < 80:
            self.state = BACK_OFF
            self.entered = now
            return BACK_OFF

        dt = now - self.entered
        if self.state in (IDLE, STOP, DOCK, BACK_OFF) and dt > 0.4:
            self.state = CREEP_FORWARD
            self.entered = now
            return CREEP_FORWARD
        if self.state == CREEP_FORWARD and dt > 1.2:
            self.state = LOOK_UP if int(now) % 2 == 0 else LOOK_DOWN
            self.entered = now
            return self.state
        if self.state in (LOOK_UP, LOOK_DOWN) and dt > 0.5:
            self.state = TURN_LEFT if self.turn_left else TURN_RIGHT
            self.turn_left = not self.turn_left
            self.entered = now
            return self.state
        if self.state in (TURN_LEFT, TURN_RIGHT) and dt > 0.6:
            self.state = CREEP_FORWARD
            self.entered = now
            return CREEP_FORWARD
        return self.state
