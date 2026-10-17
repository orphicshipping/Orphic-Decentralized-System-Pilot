"""Executable reference for a future digital FPGA capture profile.

Python integer ticks model a producer clock. This is NOT HDL, synthesis, a board
binding, timing qualification or ADC simultaneity. It specifies pre/post-trigger
semantics and explicit finite-buffer loss without using host wall-clock time.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy

from . import link_contract as c


class CaptureModel:
    def __init__(self, *, channels, pretrigger, posttrigger, capacity):
        if type(channels) is not list or not channels or len(channels) > 16 or len(channels) != len(set(channels)):
            raise ValueError("Capture model requires unique declared channels")
        for channel in channels:
            c.text(channel)
        c.integer(pretrigger, maximum=128); c.integer(posttrigger, maximum=128)
        c.integer(capacity, minimum=1, maximum=256)
        if pretrigger + 1 + posttrigger > capacity:
            raise ValueError("Requested capture window exceeds the physical-buffer declaration")
        self.channels = tuple(channels)
        self.pretrigger = pretrigger
        self.posttrigger = posttrigger
        self.capacity = capacity
        self.history = deque(maxlen=pretrigger)
        self.samples = []
        self.last_tick = None
        self.trigger_tick = None
        self.state = "ARMED"
        self.discarded_before_window = 0
        self.rejected_after_freeze = 0

    def sample(self, tick, values, *, trigger=False):
        c.integer(tick)
        c.keys(values, set(self.channels))
        for value in values.values():
            c.integer(value, maximum=2**32 - 1)
        if type(trigger) is not bool:
            raise ValueError("Trigger must be explicit boolean")
        if self.last_tick is not None and tick <= self.last_tick:
            raise ValueError("Tick rollback/wrap requires an explicit new capture/boot")
        self.last_tick = tick
        if self.state == "FROZEN":
            self.rejected_after_freeze += 1
            return "NOT_RECORDED_FROZEN"
        item = {"tick": tick, "values": deepcopy(values)}
        if self.state == "ARMED" and not trigger:
            if len(self.history) == self.pretrigger:
                self.discarded_before_window += 1
            self.history.append(item)
            return "PRETRIGGER_WINDOW"
        if self.state == "ARMED":
            self.samples = list(self.history) + [item]
            self.trigger_tick = tick
            self.state = "FROZEN" if self.posttrigger == 0 else "POSTTRIGGER"
        else:
            self.samples.append(item)
            if len([s for s in self.samples if s["tick"] > self.trigger_tick]) == self.posttrigger:
                self.state = "FROZEN"
        if len(self.samples) > self.capacity:
            raise ValueError("Capture capacity invariant violated")
        return self.state

    def snapshot(self):
        return {"schema": "ciw.link-capture-model.v1", "origin": "synthetic_reference_model",
                "state": self.state, "channels": list(self.channels), "trigger_tick": self.trigger_tick,
                "pretrigger": self.pretrigger, "posttrigger": self.posttrigger, "capacity": self.capacity,
                "samples": deepcopy(self.samples), "discarded_before_selected_window": self.discarded_before_window,
                "rejected_after_freeze": self.rejected_after_freeze,
                "hardware_timing_qualification": "not_performed", "physical_acquisition": "not_performed"}
