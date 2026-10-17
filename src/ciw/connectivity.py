"""Bounded, data-only connectivity evidence. No network or radio operations.

Profiles select aggregate AMF gauges from explicitly supplied Prometheus text.
Unselected lines are intentionally not retained. A hash of that whole export is
only a provenance declaration when its original bytes are not supplied.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from .control_contracts import bytes_ref, content_ref, json_tree, keys
from .core.identities import canonical_json, content_identity

OPERATION = "connectivity.observe.v1"
CONTEXT_SCHEMA = "ciw.connectivity-context.v1"
REQUEST_SCHEMA = "ciw.connectivity-request.v1"
MAX_EXPORT_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 256 * 1024
MAX_LINES = 16384
MAX_SELECTED = 32
MAX_LINKS = 64
MAX_NS = 2**63 - 1
OPEN5GS_REVISION = "5877b43196fca0b185b266f6616979c86c962a4c"
FREE5GC_AMF_REVISION = "602c7ea4921a9e5094b1635e862abf5566b6b3f2"
ACCESS_TYPES = ("3GPP_ACCESS", "NON_3GPP_ACCESS")
AUTHORITY = {
    "core_execution": "not_performed", "network_configuration": "not_performed",
    "subscriber_provisioning": "not_performed", "radio_transmission": "not_performed",
    "source_authentication": "not_established", "physical_qualification": "not_established",
    "clock_synchronization": "not_established", "state_admission": "not_performed",
    "equipment_control": "not_performed",
}
_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
_SAMPLE = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^\n\r}]*\})?[ \t]+(" + _NUMBER + r")[ \t]*")
_ALIAS = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,95}")


def alias(value: object) -> str:
    if (type(value) is not str or _ALIAS.fullmatch(value) is None
            or value.lower().startswith(("imsi", "supi", "suci", "imei", "msisdn"))):
        raise ValueError("Use bounded local aliases, not subscriber identifiers or addresses")
    return value


def integer(value: object, minimum: int = 0, maximum: int = MAX_NS) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Require a bounded integer, not a boolean")
    return value


def validate_context(value: dict) -> dict:
    keys(value, {"schema", "provider", "component", "provider_revision", "instance_id",
                 "network_id", "core_mode", "plmn", "data_network", "slice",
                 "configuration_ref", "metrics_namespace"})
    if (value["schema"] != CONTEXT_SCHEMA or value["provider"] not in ("open5gs", "free5gc")
            or value["component"] != "amf" or value["core_mode"] != "5g_sa"):
        raise ValueError("V1 supports Open5GS/free5GC 5G SA AMF aggregate metrics only")
    revision = value["provider_revision"]
    if revision is not None and (type(revision) is not str or re.fullmatch("[0-9a-f]{40}", revision) is None):
        raise ValueError("Declare an exact component revision or explicit null")
    for field in ("instance_id", "network_id", "data_network"):
        alias(value[field])
    keys(value["plmn"], {"mcc", "mnc"})
    for field, pattern in (("mcc", r"[0-9]{3}"), ("mnc", r"[0-9]{2,3}")):
        if type(value["plmn"][field]) is not str or re.fullmatch(pattern, value["plmn"][field]) is None:
            raise ValueError("PLMN codes must be digit strings preserving leading zeroes")
    if value["slice"] is not None:
        keys(value["slice"], {"sst", "sd"})
        integer(value["slice"]["sst"], 0, 255)
        sd = value["slice"]["sd"]
        if sd is not None and (type(sd) is not str or re.fullmatch("[0-9a-fA-F]{6}", sd) is None):
            raise ValueError("Slice differentiator must be six hexadecimal digits or null")
    content_ref(value["configuration_ref"])
    namespace = value["metrics_namespace"]
    if type(namespace) is not str or re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}|", namespace) is None:
        raise ValueError("Require a bounded, explicit Prometheus namespace")
    if value["provider"] == "open5gs" and namespace != "":
        raise ValueError("The inspected Open5GS AMF metric names have no namespace prefix")
    return deepcopy(value)


def metric_names(context: dict) -> tuple[str, ...]:
    context = validate_context(context)
    if context["provider"] == "open5gs":
        return ("ran_ue", "amf_session", "gnb")
    prefix = context["metrics_namespace"]
    return ((prefix + "_" if prefix else "") + "amf_business_ue_connectivity",)


def _selected_line(line: str, names: tuple[str, ...]) -> tuple | None:
    """A deliberately narrow gauge profile, not a general Prometheus parser."""
    text = line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line
    if any(ord(char) < 32 and char != "\t" for char in text) or len(line) > 1024:
        raise ValueError("Selected metric line is invalid or too long")
    if text.startswith("# TYPE "):
        parts = text.split()
        if len(parts) >= 3 and parts[2] in names:
            if len(parts) != 4 or parts[3] != "gauge":
                raise ValueError("Selected AMF metric must declare TYPE gauge")
            return ("type", parts[2])
        return None
    match_name = re.match(r"([a-zA-Z_:][a-zA-Z0-9_:]*)", text)
    if match_name is None or match_name[1] not in names:
        return None
    match = _SAMPLE.fullmatch(text)
    if match is None:
        raise ValueError("Selected gauge must be finite scalar text without timestamps or exemplars")
    name, label_text, token = match.groups()
    if name in ("ran_ue", "amf_session", "gnb"):
        if label_text not in (None, "{}"):
            raise ValueError("Open5GS aggregate gauges must not carry labels")
        labels = {}
    else:
        label = re.fullmatch(r'\{[ \t]*access_type[ \t]*=[ \t]*"(3GPP_ACCESS|NON_3GPP_ACCESS)"[ \t]*\}', label_text or "")
        if label is None:
            raise ValueError("Only the inspected access_type aggregate label is allowed")
        labels = {"access_type": label[1]}
    try:
        number = Decimal(token)
    except InvalidOperation as exc:
        raise ValueError("Malformed aggregate gauge") from exc
    if not number.is_finite() or number < 0 or number > 2**53 - 1 or number != number.to_integral_value():
        raise ValueError("Aggregate count must be an exact nonnegative bounded integer")
    return ("sample", name, labels, int(number))


def project_metrics(raw: bytes, context: dict) -> dict:
    """Retain exact allowlisted lines, never subscriber/config/HELP content."""
    names = metric_names(context)
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_EXPORT_BYTES:
        raise ValueError("Metrics export must be nonempty bytes within 1 MiB")
    lines = raw.decode("utf-8").splitlines(keepends=True)
    if len(lines) > MAX_LINES:
        raise ValueError("Metrics export exceeds line budget")
    selected = []
    for index, line in enumerate(lines, 1):
        # Inspect a bounded prefix before considering the full line. Unknown
        # metric names and their labels/comments are deliberately not retained.
        if _selected_line(line, names) is not None:
            selected.append({"line": index, "text": line})
            if len(selected) > MAX_SELECTED:
                raise ValueError("Too many selected metric lines")
    result = {"schema": "ciw.connectivity-metrics-projection.v1", "input_ref": bytes_ref(raw),
              "input_bytes": len(raw), "input_lines": len(lines), "selected_lines": selected,
              "whole_export_retained": False}
    validate_projection(result, context)
    return result


def validate_projection(value: dict, context: dict) -> list[dict]:
    keys(value, {"schema", "input_ref", "input_bytes", "input_lines", "selected_lines", "whole_export_retained"})
    if value["schema"] != "ciw.connectivity-metrics-projection.v1" or value["whole_export_retained"] is not False:
        raise ValueError("Require the explicit selected-line retention policy")
    content_ref(value["input_ref"])
    integer(value["input_bytes"], 1, MAX_EXPORT_BYTES)
    integer(value["input_lines"], 1, min(MAX_LINES, value["input_bytes"]))
    lines = value["selected_lines"]
    if type(lines) is not list or len(lines) > MAX_SELECTED:
        raise ValueError("Invalid selected-line budget")
    names = metric_names(context)
    previous, size, types, samples, identities = 0, 0, set(), [], set()
    for item in lines:
        keys(item, {"line", "text"})
        integer(item["line"], previous + 1, value["input_lines"])
        previous = item["line"]
        if type(item["text"]) is not str:
            raise ValueError("Selected line must be text")
        size += len(item["text"].encode("utf-8"))
        parsed = _selected_line(item["text"], names)
        if parsed is None:
            raise ValueError("Projection contains a non-allowlisted line")
        if parsed[0] == "type":
            if parsed[1] in types:
                raise ValueError("Duplicate TYPE declaration")
            types.add(parsed[1])
        else:
            _, name, labels, count = parsed
            identity = (name, canonical_json(labels))
            if name not in types or identity in identities:
                raise ValueError("Gauge lacks preceding TYPE or duplicates an aggregate")
            identities.add(identity)
            samples.append({"name": name, "labels": labels, "value": count, "unit": "1",
                            "kind": "gauge", "source_line": item["line"]})
    if size > value["input_bytes"]:
        raise ValueError("Selected bytes exceed the declared original export")
    return samples


def validate_request(value: dict) -> dict:
    json_tree(value)
    if len(canonical_json(value).encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("Connectivity request exceeds 256 KiB")
    keys(value, {"schema", "context", "source_kind", "capture", "metrics", "transport_links"})
    if value["schema"] != REQUEST_SCHEMA or value["source_kind"] not in ("synthetic", "operator_export"):
        raise ValueError("Unsupported connectivity request or source kind")
    validate_context(value["context"])
    capture = value["capture"]
    keys(capture, {"method", "observed_at", "clock_id", "clock_uncertainty_ns", "elapsed_ns"})
    if capture["method"] not in ("synthetic", "operator_file", "loopback_http"):
        raise ValueError("Unknown capture method")
    if (capture["method"] == "synthetic") != (value["source_kind"] == "synthetic"):
        raise ValueError("Synthetic and operator capture provenance cannot be mixed")
    if type(capture["observed_at"]) is not str or len(capture["observed_at"]) > 40:
        raise ValueError("Require a bounded timezone-aware capture timestamp")
    stamp = datetime.fromisoformat(capture["observed_at"].replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Capture timestamp requires an explicit timezone")
    alias(capture["clock_id"])
    for field in ("clock_uncertainty_ns", "elapsed_ns"):
        if capture[field] is not None:
            integer(capture[field])
    validate_projection(value["metrics"], value["context"])
    links = value["transport_links"]
    if type(links) is not list or len(links) > MAX_LINKS:
        raise ValueError("Transport link budget exceeded")
    seen = set()
    for link in links:
        keys(link, {"link_id", "observation_ref", "device_id", "network_session_alias", "edge_id",
                    "sample_clock_id", "receive_clock_id", "sample_time_ns", "receive_time_ns"})
        for field in ("link_id", "device_id", "network_session_alias", "edge_id", "sample_clock_id", "receive_clock_id"):
            alias(link[field])
        if link["link_id"] in seen:
            raise ValueError("Duplicate transport link identity")
        seen.add(link["link_id"])
        content_ref(link["observation_ref"])
        integer(link["sample_time_ns"])
        integer(link["receive_time_ns"])
    return deepcopy(value)


def observe(value: dict) -> dict:
    request = validate_request(value)
    samples = validate_projection(request["metrics"], request["context"])
    expected = [(name, {}) for name in metric_names(request["context"])]
    if request["context"]["provider"] == "free5gc":
        expected = [(metric_names(request["context"])[0], {"access_type": access}) for access in ACCESS_TYPES]
    actual = {(row["name"], canonical_json(row["labels"])) for row in samples}
    missing = [{"name": name, "labels": labels} for name, labels in expected if (name, canonical_json(labels)) not in actual]
    links = []
    for link in request["transport_links"]:
        same_clock = link["sample_clock_id"] == link["receive_clock_id"]
        delta = link["receive_time_ns"] - link["sample_time_ns"] if same_clock else None
        links.append({**link, "sample_to_receive_delta_ns": delta, "uncertainty_ns": None,
                      "status": "CLOCK_RELATION_UNKNOWN" if delta is None else
                      "INCONSISTENT_DECLARED_ORDER" if delta < 0 else "SAME_DECLARED_CLOCK",
                      "scope": "declared_timestamp_difference_not_network_latency",
                      "observation_resolution": "reference_only_not_loaded_or_authenticated"})
    return {"schema": "ciw.connectivity-observation.v1", "request_ref": content_identity(request),
            "status": "MISSING_METRICS" if missing else "OBSERVED",
            "source_kind": request["source_kind"], "context": request["context"],
            "capture": request["capture"], "metrics": samples, "missing_metrics": missing,
            "projection_ref": content_identity(request["metrics"]), "transport_links": links,
            "scope": "aggregate_export_and_declared_transport_context_only",
            "whole_export_integrity": "unverifiable_without_original_export_bytes",
            "capture_elapsed_semantics": "collector_request_duration_not_ue_latency",
            "authority": deepcopy(AUTHORITY)}


def example_context(provider: str = "open5gs") -> dict:
    context = {"schema": CONTEXT_SCHEMA, "provider": provider, "component": "amf",
               "provider_revision": OPEN5GS_REVISION if provider == "open5gs" else FREE5GC_AMF_REVISION,
               "instance_id": "synthetic.amf.1", "network_id": "synthetic.network.1", "core_mode": "5g_sa",
               "plmn": {"mcc": "001", "mnc": "01"}, "data_network": "instrument",
               "slice": {"sst": 1, "sd": "010203"},
               "configuration_ref": content_identity({"synthetic": "no_core_configuration"}),
               "metrics_namespace": "" if provider == "open5gs" else "free5gc"}
    return validate_context(context)


def example_metrics(provider: str = "open5gs") -> bytes:
    context = example_context(provider)
    if provider == "open5gs":
        return b"# TYPE ran_ue gauge\nran_ue 2\n# TYPE amf_session gauge\namf_session 1\n# TYPE gnb gauge\ngnb 1\n"
    name = metric_names(context)[0]
    return (f'# TYPE {name} gauge\n{name}{{access_type="3GPP_ACCESS"}} 2\n'
            f'{name}{{access_type="NON_3GPP_ACCESS"}} 0\n').encode()


def example_request(provider: str = "open5gs") -> dict:
    context = example_context(provider)
    return validate_request({"schema": REQUEST_SCHEMA, "context": context, "source_kind": "synthetic",
        "capture": {"method": "synthetic", "observed_at": "2026-10-05T00:00:00Z",
                    "clock_id": "synthetic.capture-clock.1", "clock_uncertainty_ns": None, "elapsed_ns": None},
        "metrics": project_metrics(example_metrics(provider), context),
        "transport_links": [{"link_id": "synthetic.link.1", "observation_ref": content_identity({"synthetic_observation": 1}),
            "device_id": "synthetic.handheld.1", "network_session_alias": "synthetic.pdu.1", "edge_id": "synthetic.edge.1",
            "sample_clock_id": "synthetic.handheld-clock.1", "receive_clock_id": "synthetic.edge-clock.1",
            "sample_time_ns": 1000000000, "receive_time_ns": 1030000000}]})


def catalog() -> dict:
    return {"schema": "ciw.connectivity-catalog.v1", "operation": OPERATION,
        "execution": "explicit_local_Session_binding", "live_capture": "explicit_loopback_http_get_only",
        "profiles": [
            {"provider": "open5gs", "component": "amf", "source_revision": OPEN5GS_REVISION,
             "source": "https://github.com/open5gs/open5gs/blob/" + OPEN5GS_REVISION + "/src/amf/metrics.c",
             "source_blob": "804b73af62370ee5823cb3500874dd3720cee244", "metrics": list(metric_names(example_context()))},
            {"provider": "free5gc", "component": "amf", "source_revision": FREE5GC_AMF_REVISION,
             "source": "https://github.com/free5gc/amf/blob/" + FREE5GC_AMF_REVISION + "/internal/metrics/business/types.go",
             "source_blob": "393fae5f0a846207005bdef3c58e143325920ecc",
             "metrics": ["<explicit_namespace>_amf_business_ue_connectivity"], "labels": {"access_type": list(ACCESS_TYPES)}}],
        "provider_qualification": "source_schema_review_only_not_native_core_execution",
        "unsupported": ["LTE_MME_metrics", "subscriber_records", "core_lifecycle", "RAN", "RF", "SIM_provisioning",
                        "inter_clock_mapping", "UE_latency", "network_slicing_changes"],
        "authority": deepcopy(AUTHORITY)}
