"""Operator-bound MCP adapter for the existing measured thermal workflow.

The run envelope is a transport declaration, not another temperature recording.
Exact ingress bytes and the original workflow's scientific identities, conditional
uncertainty and authority remain inside the retained operation result.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from . import measured_thermal as contract
from . import measured_thermal_workflow as workflow
from .adapters.protocol import InstrumentManifest
from .agent_api import AgentHost
from .control_contracts import bytes_ref, keys
from .core.identities import content_identity, evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .operations.registry import Operation

# A retained native bundle embeds these exact bytes in one base64 string.
# Keep that string within the existing 65,536-character control JSON bound.
SOURCE_LIMIT = 48 * 1024
ASSESS = "measured-thermal.assess.v1"
PAYLOAD_SCHEMA = "ciw.measured-thermal-agent-result.v1"
CORRECTION_STATUS = "not_checked; no_state_ledger_handoff_performed"


def make_source(raw: bytes) -> dict:
    """Wrap at most 48 KiB of exact operator source bytes without inference."""
    if type(raw) is not bytes or not 0 < len(raw) <= SOURCE_LIMIT:
        raise ValueError("Measured thermal MCP source must contain 1..49152 bytes (48 KiB)")
    measured = contract.validate_source(raw)
    manifest = InstrumentManifest(
        instrument_id="measured-thermal-evidence.v1", role="retained_thermal_recording",
        units={"recording_declaration": "1"}, frames=(measured["frame"],),
        sampling={"kind": "one_recording_declaration", "time_semantics": "synthetic_transport_envelope"},
        supported_operations=(ASSESS,),
        calibration_requirements={"scope": "already_applied; declared_historical_applicability_only"})
    source = {
        "run_schema": "run.v1", "run_id": "run-measured-thermal-" + sha256(raw).hexdigest()[:24],
        "instrument": manifest.instrument_id,
        "metadata": {"duration_s": 1.0, "sample_count": 1, "sample_rate_hz": None,
                     "coordinate_frame": measured["frame"], "manifest": manifest.to_dict(),
                     "measured_thermal_source": {"sha256": bytes_ref(raw),
                                                "bytes_b64": base64.b64encode(raw).decode("ascii")},
                     "provenance": {"source": measured["origin"],
                                    "generator": "ciw.measured_thermal_agent.make_source", "generator_version": 1}},
        "time_s": [0.0], "channels": {"recording_declaration": {"unit": "1", "values": [1.0]}},
        "render": {},
    }
    source["evidence_id"] = evidence_id(source)
    return source


def source_bytes(source: dict) -> bytes:
    validate_run_structure(source)
    validate_evidence_identity(source)
    retained = source["metadata"]["measured_thermal_source"]
    keys(retained, {"sha256", "bytes_b64"})
    encoded = retained["bytes_b64"]
    if type(encoded) is not str or len(encoded) > 4 * ((SOURCE_LIMIT + 2) // 3):
        raise ValueError("Require bounded exact measured thermal source bytes")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Measured thermal source requires canonical base64") from exc
    if source != make_source(raw):
        raise ValueError("Measured thermal transport differs from its exact source evidence")
    return raw


def runtime_identity() -> dict:
    adapter = Path(__file__).read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
    return {"provider": "ciw.measured_thermal_agent", "version": "1",
            "adapter_sha256": sha256(adapter).hexdigest(), "source_normalization": "utf8_lf",
            "workflow": workflow.runtime_identity(), "scope": workflow.EXECUTION_SCOPE}


def _assess(source: dict, parameters: dict) -> dict:
    keys(parameters, set())
    raw = source_bytes(source)
    bundle = workflow.MeasuredThermalWorkflow().create_session(raw, {})
    return {"schema": PAYLOAD_SCHEMA, "source_ref": bytes_ref(raw),
            "source_origin": contract.validate_source(raw)["origin"], "bundle": bundle,
            "correction_status": CORRECTION_STATUS, "authority": deepcopy(workflow.AUTHORITY)}


def validate_payload(operation: str, data: dict, source: dict, parameters: dict, selection: dict) -> None:
    """Read retained evidence using the existing workflow's offline validator."""
    keys(parameters, set())
    keys(data, {"schema", "source_ref", "source_origin", "bundle", "correction_status", "authority"})
    raw = source_bytes(source)
    if (operation != ASSESS or data["schema"] != PAYLOAD_SCHEMA or data["source_ref"] != bytes_ref(raw)
            or data["source_origin"] != contract.validate_source(raw)["origin"]
            or data["authority"] != workflow.AUTHORITY or data["correction_status"] != CORRECTION_STATUS):
        raise ValueError("Measured thermal result source or authority binding differs")
    retained_raw = workflow.MeasuredThermalWorkflow()._validate(data["bundle"])
    if retained_raw != raw:
        raise ValueError("Measured thermal result differs from the exact bound recording")


def capability_registry(*, bind: bool = False):
    from .control_plane import CapabilityRegistry
    value = CapabilityRegistry()
    operation = Operation(ASSESS, "state_estimator", _assess, runtime_identity)
    manifest = InstrumentManifest(
        instrument_id=ASSESS, role=operation.role, units={}, frames=(),
        sampling={"kind": "retained_calibrated_thermal_recordings"}, supported_operations=(ASSESS,),
        calibration_requirements={"scope": "declared_historical_applicability_only",
                                  "physical_validation": "not_established"})
    value.advertise(manifest, runtime=runtime_identity(),
                    capabilities={ASSESS: ["measured-thermal.assess"]}, inputs={ASSESS: {}})
    if bind:
        value.bind(operation)
    return value


def validate_graph(value: dict, registry) -> None:
    from .control_plane import plan_graph
    plan_graph(value, registry)
    if value["parameters"] != {}:
        raise ValueError("Measured thermal graph cannot add literal operation parameters")
    for node in value["nodes"]:
        if node["operation_id"] != ASSESS or node["parameters"] != {} or node["inputs"] != {}:
            raise ValueError("Measured thermal assessment reads its exact bound source only")


class MeasuredThermalHost(AgentHost):
    """Check each selected graph against its exact source before recording an attempt."""

    def execute(self, source: str, graph: str, attempt: str) -> dict:
        source_value, graph_value = self._get(source), self._get(graph)
        measured = contract.validate_source(source_bytes(source_value))
        validate_graph(graph_value, self._registry)
        if graph_value["model_id"] != content_identity(measured["request"]["model"]):
            raise ValueError("Measured thermal graph model differs from the bound recording model")
        return super().execute(source, graph, attempt)
