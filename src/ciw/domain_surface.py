"""Provider-free synthetic solvers as one MCP call.

Importing this module does not run a solver. A result is a bounded synthetic
calculation. It is not a measurement, a forecast, or a physical qualification.
Pinned session workflows, SP1, Julia, Godot, Bevy, and Blender are not here.
"""
from __future__ import annotations

from hashlib import sha256
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


def _atmosphere(_name):
    from .atmosphere_contract import example_request
    from .atmosphere_workflow import run
    return _bundle(run, example_request())


def _moist(_name):
    from .atmosphere_moist_contract import example_request
    from .atmosphere_workflow import run
    return _bundle(run, example_request())


def _weather(name):
    from .weather_contract import example_request
    from .weather_workflow import run
    return _bundle(run, example_request(name))


def _impact(name):
    from .impact_workflow import run
    if name == "contact":
        from .impact_contract import example_request
    elif name == "plate":
        from .impact_plate_contract import example_request
    elif name == "crush":
        from .impact_crush_contract import example_request
    else:
        raise ValueError("Impact name must be contact, plate, or crush")
    return _bundle(run, example_request())


def _irrigation(_name):
    from .irrigation_contract import example_request
    from .irrigation_workflow import run
    return _bundle(run, example_request())


def _geomatics(name):
    from .core.identities import canonical_json
    from .geomatics_workflow import example
    from net_geomatics.catalog import execute, validate_output
    request = example(name)
    output = execute(name, request["parameters"])
    validate_output(name, request["parameters"], output)
    return _stamp({
        "status": "PASS",
        "output_digest": sha256(canonical_json(output).encode()).hexdigest(),
        "field_retained": False,
        "authority": {"physical_validation": "not_established", "source_authenticity": "not_established"},
    })


def _thermo(name):
    from .thermofluids_models import example_request
    from .thermofluids_workflow import run, verify
    record = run(example_request(name))
    report = verify(record)
    return _stamp({
        "status": report["status"],
        "record_digest": record["record_digest"],
        "authority": record["authority"],
        "data_retained": False,
    })


def _sequence(_name):
    from .sequence_kinetics import example_request, analyze
    report = analyze(example_request())
    return _stamp({
        "status": report["status"],
        "schema": report["schema"],
        "source_kind": report["source_kind"],
        "markov_entropy_rate_nats_per_symbol": report["markov_entropy_rate_nats_per_symbol"],
    })


def _identify(_name):
    from .thermal_identification import example_request, fit
    report = fit(example_request())
    return _stamp({
        "status": report["status"],
        "schema": report["schema"],
        "authority": report["authority"],
        "record_digest": report["record_digest"],
        "residuals_retained": False,
    })


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


def run(domain, name=None):
    if domain not in _CALLS:
        raise ValueError("Unknown domain. Call ncp_domain_list.")
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
        body = _CALLS[domain](name)
    except ValueError:
        raise
    except (KeyError, TypeError, OSError) as exc:
        raise ValueError(str(exc)) from exc
    body["domain"] = domain
    body["name"] = name
    return body
