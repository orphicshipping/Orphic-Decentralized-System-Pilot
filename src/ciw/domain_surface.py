"""Provider-free synthetic solvers as one MCP call.

Importing this module does not run a solver. A result is a bounded synthetic
calculation. It is not a measurement, a forecast, or a physical qualification.
Pinned session workflows, SP1, Julia, Godot, Bevy, and Blender are not here.
"""
from __future__ import annotations

from hashlib import sha256
import json
import shutil
import tempfile
from pathlib import Path

DOMAINS = (
    "atmosphere",
    "atmosphere-moist",
    "weather",
    "impact",
    "irrigation",
    "geomatics",
    "thermofluids",
    "sequence",
    "thermal-identification",
)

_WEATHER = (
    "thermodynamics", "radiation", "radar_cloud", "synoptic", "nwp_transport",
    "evapotranspiration", "water_balance", "snow_ice", "polar_radiation",
    "ice_growth", "storm_sounding", "forecast_verification",
)
_IMPACT = (
    ("contact", "impact.contact.v1", "Synthetic elastic contact. Not a physical impact test."),
    ("plate", "impact.plate.v1", "Synthetic modal plate contact. The field is not retained."),
    ("crush", "impact.crush.v1", "Synthetic elastic-plastic crush. Not a physical impact test."),
)
_GEOMATICS = (
    "geomatics.atmosphere.optical-depth.v1",
    "geomatics.atmosphere.transmission.v1",
    "geomatics.coordinates.enu.v1",
    "geomatics.image.accuracy.v1",
    "geomatics.image.change.v1",
    "geomatics.image.classify.v1",
    "geomatics.image.focal.v1",
    "geomatics.image.normalized-difference.v1",
    "geomatics.image.radiometry.v1",
    "geomatics.radiometry.brightness-temperature.v1",
    "geomatics.radiometry.planck.v1",
    "geomatics.raster.suitability.v1",
    "geomatics.raster.zonal.v1",
    "geomatics.terrain.slope.v1",
    "geomatics.urban.accessibility.v1",
    "geomatics.urban.indicators.v1",
)
_THERMO = ("convection", "heat-exchanger", "pipe-flow", "radiation", "two-phase")

# capability_id, domain, name or None, summary
OPERATIONS = (
    ("atmosphere.compile.v1", "atmosphere", None,
     "Call ncp_domain_run domain=atmosphere. Bounded dry hydrostatic column. Not a field campaign."),
    ("atmosphere.moist.compile.v1", "atmosphere-moist", None,
     "Call ncp_domain_run domain=atmosphere-moist. Bounded moist column. Not a field campaign."),
    *[
        (f"weather.{name}.v1", "weather", name,
         f"Call ncp_domain_run domain=weather name={name}. Synthetic profile. Not a forecast.")
        for name in _WEATHER
    ],
    *[
        (capability_id, "impact", name, f"Call ncp_domain_run domain=impact name={name}. {summary}")
        for name, capability_id, summary in _IMPACT
    ],
    ("irrigation.plan.v1", "irrigation", None,
     "Call ncp_domain_run domain=irrigation. Synthetic irrigation plan. Not a field application."),
    *[
        (name, "geomatics", name,
         f"Call ncp_domain_run domain=geomatics name={name}. Synthetic reference. The field is not retained.")
        for name in _GEOMATICS
    ],
    *[
        (f"thermofluids.{name}.v1", "thermofluids", name,
         f"Call ncp_domain_run domain=thermofluids name={name}. Synthetic balance. Not a plant measurement.")
        for name in _THERMO
    ],
    ("sequence.markov.v1", "sequence", None,
     "Call ncp_domain_run domain=sequence. Synthetic Markov block. Not an observed sequence."),
    ("thermal.identify.v1", "thermal-identification", None,
     "Call ncp_domain_run domain=thermal-identification. Synthetic identification candidate. Not a calibration."),
)

_BY_ID = {row[0]: row for row in OPERATIONS}
if len(_BY_ID) != len(OPERATIONS):
    raise RuntimeError("duplicate domain operation")


def tool_grants():
    return {row[0]: "ncp_domain_run" for row in OPERATIONS}


def listing():
    return {
        "schema": "ncp.domain-catalog.v1",
        "authorizes_execution": False,
        "physical_qualification": "not_established",
        "operations": [
            {"capability_id": capability_id, "domain": domain, "name": name, "summary": summary}
            for capability_id, domain, name, summary in OPERATIONS
        ],
        "not_executed_here": [
            "Session workflows that require a provider pin stay on ncp_workflows_list.",
            "proved_heat is ncp_ledger_read, not an SP1 proof.",
            "Julia, Godot, Bevy, and Blender are not bound.",
        ],
    }


def _stamp(body):
    body["physical_qualification"] = "not_established"
    body["synthetic"] = True
    body["authorizes_execution"] = False
    return body


def _checks(report):
    rows = []
    for item in report.get("checks") or []:
        if type(item) is dict and type(item.get("name")) is str and type(item.get("status")) is str:
            rows.append({"name": item["name"], "status": item["status"]})
    qualification = report.get("qualification") or {}
    return _stamp({
        "status": report.get("status"),
        "checks": rows,
        "qualification_action": qualification.get("action"),
        "qualification_reason": qualification.get("reason"),
        "authority": report.get("authority"),
        "bundle_retained": False,
    })


def _bundle(run, request):
    parent = Path(tempfile.mkdtemp(prefix="ncp-domain-"))
    try:
        return _checks(run(request, parent / "out"))
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def _retain(kind, value):
    from .retention import keep
    if type(value) is not dict:
        raise ValueError("Domain result is not a record")
    try:
        json.dumps(value)
    except TypeError:
        value = json.loads(json.dumps(value, default=lambda item: item.tolist() if hasattr(item, "tolist") else item.item()))
    return keep(value, kind=kind, catalog={"schema": "ncp.domain-result.v1", "kind": kind})


def _prepared(request, example, factory, validate):
    if example is True and request is not None:
        raise ValueError("Name a request or an example, not both.")
    if request is None:
        if example is not True:
            raise ValueError("Name a request or set example true. This call does not substitute an example.")
        return factory(), True
    if type(request) is not dict:
        raise ValueError("Domain request must be an object")
    return validate(request), False


def _atmosphere(_name, request=None, example=False):
    from .atmosphere_contract import example_request, validate_request
    from .atmosphere_workflow import run
    prepared, used_example = _prepared(request, example, example_request, validate_request)
    parent = Path(tempfile.mkdtemp(prefix="ncp-domain-"))
    try:
        report = run(prepared, parent / "out")
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    body = _checks(report)
    body["artifact_id"] = _retain("atmosphere", report)
    body["bundle_retained"] = True
    body["example"] = used_example
    return body


def _moist(_name, request=None, example=False):
    from .atmosphere_moist_contract import example_request, validate_request
    from .atmosphere_workflow import run
    prepared, used_example = _prepared(request, example, example_request, validate_request)
    parent = Path(tempfile.mkdtemp(prefix="ncp-domain-"))
    try:
        report = run(prepared, parent / "out")
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    body = _checks(report)
    body["artifact_id"] = _retain("atmosphere-moist", report)
    body["bundle_retained"] = True
    body["example"] = used_example
    return body


def _weather(name, request=None, example=False):
    from .weather_contract import example_request, validate_request
    from .weather_workflow import run
    prepared, used_example = _prepared(request, example, lambda: example_request(name), validate_request)
    parent = Path(tempfile.mkdtemp(prefix="ncp-domain-"))
    try:
        report = run(prepared, parent / "out")
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    body = _checks(report)
    body["artifact_id"] = _retain("weather", report)
    body["bundle_retained"] = True
    body["example"] = used_example
    return body


def _impact(name, request=None, example=False):
    from .impact_workflow import run
    if name == "contact":
        from .impact_contract import example_request, validate_request
    elif name == "plate":
        from .impact_plate_contract import example_request, validate_request
    elif name == "crush":
        from .impact_crush_contract import example_request, validate_request
    else:
        raise ValueError("Impact name must be contact, plate, or crush")
    prepared, used_example = _prepared(request, example, example_request, validate_request)
    parent = Path(tempfile.mkdtemp(prefix="ncp-domain-"))
    try:
        report = run(prepared, parent / "out")
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    body = _checks(report)
    body["artifact_id"] = _retain("impact", report)
    body["bundle_retained"] = True
    body["example"] = used_example
    return body


def _irrigation(_name, request=None, example=False):
    from .irrigation_contract import example_request, validate_request
    from .irrigation_workflow import run
    prepared, used_example = _prepared(request, example, example_request, validate_request)
    parent = Path(tempfile.mkdtemp(prefix="ncp-domain-"))
    try:
        report = run(prepared, parent / "out")
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    body = _checks(report)
    body["artifact_id"] = _retain("irrigation", report)
    body["bundle_retained"] = True
    body["example"] = used_example
    return body


def _geomatics(name, request=None, example=False):
    from .core.identities import canonical_json
    from .geomatics_workflow import example as geomatics_example
    from net_geomatics.catalog import execute, validate_output
    if request is not None:
        raise ValueError("Geomatics on this surface takes the named example. A free request is not a declared schema.")
    if example is not True:
        raise ValueError("Name a request or set example true. This call does not substitute an example.")
    chosen = geomatics_example(name)
    output = execute(name, chosen["parameters"])
    validate_output(name, chosen["parameters"], output)
    body = _stamp({
        "status": "PASS",
        "output_digest": sha256(canonical_json(output).encode()).hexdigest(),
        "field_retained": True,
        "example": True,
        "authority": {"physical_validation": "not_established", "source_authenticity": "not_established"},
    })
    body["artifact_id"] = _retain("geomatics", {"request": chosen, "output": output})
    return body


def _thermo(name, request=None, example=False):
    from .thermofluids_models import example_request
    from .thermofluids_workflow import run, verify
    if request is not None:
        raise ValueError("Thermofluids on this surface takes the named example. A free request is not a declared schema.")
    if example is not True:
        raise ValueError("Name a request or set example true. This call does not substitute an example.")
    record = run(example_request(name))
    report = verify(record)
    body = _stamp({
        "status": report["status"],
        "record_digest": record["record_digest"],
        "authority": record["authority"],
        "data_retained": True,
        "example": True,
    })
    body["artifact_id"] = _retain("thermofluids", record)
    return body


def _sequence(_name, request=None, example=False):
    from .sequence_kinetics import example_request, analyze
    if request is not None:
        raise ValueError("Sequence on this surface takes the labelled example. A free request is not a declared schema.")
    if example is not True:
        raise ValueError("Name a request or set example true. This call does not substitute an example.")
    report = analyze(example_request())
    body = _stamp({
        "status": report["status"],
        "schema": report["schema"],
        "source_kind": report["source_kind"],
        "markov_entropy_rate_nats_per_symbol": report["markov_entropy_rate_nats_per_symbol"],
        "example": True,
    })
    body["artifact_id"] = _retain("sequence", report)
    return body


def _identify(_name, request=None, example=False):
    from .thermal_identification import example_request, fit
    if request is not None:
        raise ValueError("Thermal identification on this surface takes the labelled example. A free request is not a declared schema.")
    if example is not True:
        raise ValueError("Name a request or set example true. This call does not substitute an example.")
    report = fit(example_request())
    body = _stamp({
        "status": report["status"],
        "schema": report["schema"],
        "authority": report["authority"],
        "record_digest": report["record_digest"],
        "residuals_retained": True,
        "example": True,
    })
    body["artifact_id"] = _retain("thermal-identification", report)
    return body


_CALLS = {
    "atmosphere": _atmosphere,
    "atmosphere-moist": _moist,
    "weather": _weather,
    "impact": _impact,
    "irrigation": _irrigation,
    "geomatics": _geomatics,
    "thermofluids": _thermo,
    "sequence": _sequence,
    "thermal-identification": _identify,
}
_NAMES = {}
for _capability_id, _domain, _name, _summary in OPERATIONS:
    _NAMES.setdefault(_domain, set()).add(_name)


def run(domain, name=None, example=False, request=None):
    if domain not in _CALLS:
        raise ValueError("Unknown domain. Call ncp_domain_list.")
    if example is True and request is not None:
        raise ValueError("Name a request or an example, not both.")
    if example is not True and request is None:
        raise ValueError("Name a request or set example true. This call does not substitute an example.")
    if request is not None and len(json.dumps(request)) > 65536:
        raise ValueError("Request exceeds the argument budget")
    allowed = _NAMES[domain]
    if None in allowed:
        if name is not None:
            raise ValueError("This domain does not take a name")
    elif name is None:
        choices = ", ".join(sorted(item for item in allowed if item is not None))
        raise ValueError(
            "This domain is a set of tasks. It does not opt for the easiest one. Name one of: " + choices
        )
    elif name not in allowed:
        choices = ", ".join(sorted(item for item in allowed if item is not None))
        raise ValueError("Unknown name for " + domain + ". Use one of: " + choices)
    try:
        body = _CALLS[domain](name, request=request, example=example)
    except ValueError:
        raise
    except (KeyError, TypeError, OSError) as exc:
        raise ValueError(str(exc)) from exc
    body["domain"] = domain
    body["name"] = name
    return body
