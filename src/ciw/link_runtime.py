"""Host reference Device Runtime and simulated configuration control.

This is executable producer/gateway software, not compiled MCU firmware or a
board support package. Physical CONFIGURE/CALIBRATE/UPDATE/CONTROL dispatch is
unavailable. Host policy is a runtime object, never restored from wire data.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import uuid

from . import link_contract as c
from .link_protocol import encode


@dataclass(frozen=True)
class Authority:
    principal: str
    device_id: str
    permissions: frozenset[str]
    expires_ns: int

    def check(self, permission, device, now_ns):
        c.text(self.principal); c.text(self.device_id)
        c.integer(self.expires_ns); c.integer(now_ns)
        if (type(self.permissions) is not frozenset or not self.permissions <= c.PERMISSIONS
                or permission not in self.permissions or permission not in device["capabilities"]
                or self.device_id != device["device_id"] or now_ns >= self.expires_ns):
            raise PermissionError("Missing, expired or mismatched host-bound device permission")
        if permission == "CONTROL":
            raise PermissionError("No Link physical-control capability exists")


def prepare_configuration(device, new_configuration, *, expires_ns):
    device = c.manifest(device)
    c.integer(expires_ns)
    new_configuration = c.configuration(new_configuration, {x["id"] for x in device["channels"]})
    if new_configuration["revision"] != device["configuration"]["revision"] + 1:
        raise ValueError("Configuration revision must advance exactly once")
    value = {"schema": "ciw.link-configuration-plan.v1", "device_id": device["device_id"],
             "base_manifest_ref": c.content_identity(device), "base_configuration_ref": c.content_identity(device["configuration"]),
             "new_configuration": new_configuration, "expires_ns": expires_ns,
             "effect": "changes_acquisition_interpretation_simulated_only", "dispatch": "not_performed"}
    return {**value, "plan_ref": c.content_identity(value)}


class DeviceRuntime:
    """Reference byte producer. Firmware/gateware identities stay declarations."""
    def __init__(self, device, journal, *, boot_id=None, origin="synthetic", key=None):
        self.device = c.manifest(device)
        if origin not in {"synthetic", "recorded"}:
            raise ValueError("Reference runtime accepts synthetic or explicitly recorded input only")
        self.journal = journal
        self.origin = origin
        self.key = key
        self.boot_id = boot_id or "boot-" + uuid.uuid4().hex
        c.text(self.boot_id)
        self.sequence = 0
        self.event_sequence = 0
        # Reopen resumes the explicitly supplied boot without reusing IDs.
        from .link_store import decode_bound
        for row in journal.rows():
            if row["manifest_ref"] != c.content_identity(self.device):
                continue
            frame, value, _, _ = decode_bound(row["wire"], self.device, key=key, offline=key is None)
            if value.get("boot_id") == self.boot_id:
                if frame.kind == "observation":
                    self.sequence = max(self.sequence, value["sequence"] + 1)
                elif frame.kind == "event":
                    self.event_sequence = max(self.event_sequence, value["event_sequence"] + 1)
        journal.append(encode("manifest", c.bounded(self.device), key=key), expected_manifest=self.device, key=key)

    def _ensure_current(self):
        current = self.journal.state("config:" + self.device["device_id"])
        if current is not None and current["value"]["manifest_ref"] != c.content_identity(self.device):
            raise ValueError("Runtime configuration is stale; refusing new acquisition")

    def emit(self, channel_id, raw, *, tick=None, context=None, acquired_at=None, calibration_ref=None):
        self._ensure_current()
        value = c.make_observation(self.device, boot_id=self.boot_id, sequence=self.sequence, channel_id=channel_id,
            raw=raw, tick=tick, origin=self.origin, context=context, acquired_at=acquired_at, calibration_ref=calibration_ref)
        receipt = self.journal.append(encode("observation", c.bounded(value), key=self.key), expected_manifest=self.device, key=self.key, runtime_device_id=self.device["device_id"])
        self.sequence += 1  # Only committed bytes consume a sample occurrence.
        return {"record": value, "receipt": receipt}

    def event(self, event, detail, *, tick=None):
        self._ensure_current()
        from .link_store import validate_event
        value = {"schema": c.EVENT_SCHEMA, "device_id": self.device["device_id"], "manifest_ref": c.content_identity(self.device),
                 "boot_id": self.boot_id, "event_sequence": self.event_sequence, "event": event, "device_tick": tick, "detail": detail}
        validate_event(value, self.device)
        receipt = self.journal.append(encode("event", c.bounded(value), key=self.key), expected_manifest=self.device, key=self.key, runtime_device_id=self.device["device_id"])
        self.event_sequence += 1
        return receipt

    def configure_simulated(self, plan, authority, *, now_ns):
        if self.origin != "synthetic":
            raise PermissionError("Physical/recorded device configuration is not implemented")
        authority.check("CONFIGURE", self.device, now_ns)
        c.keys(plan, {"schema", "device_id", "base_manifest_ref", "base_configuration_ref", "new_configuration",
                      "expires_ns", "effect", "dispatch", "plan_ref"})
        expected = prepare_configuration(self.device, plan["new_configuration"], expires_ns=plan["expires_ns"])
        if c.canonical_json(plan) != c.canonical_json(expected) or now_ns >= plan["expires_ns"]:
            raise ValueError("Configuration plan changed, is stale, or expired")
        namespace = "config:" + self.device["device_id"]
        current = self.journal.state(namespace)
        if current is not None and current["value"]["manifest_ref"] != c.content_identity(self.device):
            raise ValueError("Runtime is stale relative to retained simulated configuration")
        new_device = deepcopy(self.device)
        new_device["configuration"] = plan["new_configuration"]
        c.manifest(new_device)
        next_boot = "boot-" + uuid.uuid4().hex
        review = {"plan_ref": plan["plan_ref"], "manifest_ref": c.content_identity(new_device), "boot_id": next_boot,
                  "status": "SIMULATED_CONFIGURATION_APPLIED", "principal": authority.principal,
                  "calibration_review": "required_for_new_configuration", "physical_dispatch": "not_performed",
                  "manifest": new_device, "plan": deepcopy(plan)}
        revision = 0 if current is None else current["revision"]
        self.journal.compare_and_set_state(namespace, revision, review)
        # Instantiate explicitly: new manifest/new boot, old samples unchanged.
        # Failure to append the new manifest leaves the retained state changed
        # and old runtime stale; a caller must reconstruct from this review.
        replacement = DeviceRuntime(new_device, self.journal, boot_id=next_boot, origin="synthetic", key=self.key)
        return replacement, review

    def dispatch(self, permission, payload, authority, *, now_ns):
        authority.check(permission, self.device, now_ns)
        raise PermissionError("No physical configuration, calibration, firmware/gateware update, or machine dispatch exists")
