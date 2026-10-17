"""Seven application descriptors over one Link contract, not seven hardware claims."""
from __future__ import annotations

from . import link_contract as c

_PROFILES = {
    "GaugeLink": (("length", "mm", 24.982),),
    "MachineLink": (("pressure", "Pa", 1200000.0), ("temperature", "K", 310.0)),
    "ConditionLink": (("acceleration", "m/s^2", 0.125), ("frequency", "Hz", 25.0)),
    "FieldLink": (("easting", "m", 12.0), ("northing", "m", 4.0), ("height", "m", 1.5)),
    "ThermalLink": (("temperature", "K", 296.15), ("humidity", "%", 45.0)),
    "DAQLink": (("voltage", "V", 1.25),),
    "CalLink": (("length", "mm", 25.0),),
}
_PURPOSES = {
    "GaugeLink": "QC and dimensional/force-gauge records",
    "MachineLink": "Declared machine-cycle and process observations",
    "ConditionLink": "Retained condition readings; waveform qualification remains separate",
    "FieldLink": "Spatial context; no surveyed-frame or GNSS accuracy claim",
    "ThermalLink": "Thermal/environmental records; no radiometric decoder claim",
    "DAQLink": "Raw capture and acquisition records; no ADC bandwidth claim",
    "CalLink": "Reference comparison records; no stimulus or certificate authority",
}


def catalog():
    return {"schema": "ciw.link-catalog.v1", "profiles": [
        {"name": name, "purpose": _PURPOSES[name], "contract": c.DEVICE_SCHEMA,
         "reference_example": "executable_synthetic", "recorded_scalar_decoder": "notation-ascii-scalar.v1",
         "physical_support": "no_device_model_qualified", "control": "NONE"} for name in c.PERSONAS],
        "transports": {"framed_byte_stream": "implemented", "recorded_file": "implemented",
                       "local_durable_sync": "implemented", "usb_serial_ble_can_modem": "requires_explicit_device_binding"},
        "limits": dict(c.LIMITS)}


def example_manifest(persona="GaugeLink"):
    if persona not in _PROFILES:
        raise ValueError("Unknown Link persona")
    channels = [{"id": f"CH{i}", "transducer_id": f"synthetic.sensor.{i}", "quantity": quantity,
                 "unit": unit, "frame": "synthetic.instrument.frame", "encoding": "notation-ascii-scalar.v1"}
                for i, (quantity, unit, _) in enumerate(_PROFILES[persona])]
    value = {"schema": c.DEVICE_SCHEMA, "device_id": "synthetic." + persona + ".1",
             "instrument_id": "synthetic.instrument.1", "hardware_revision": "synthetic.no-board",
             "firmware": {"version": "synthetic.no-installed-firmware", "content_ref": c.bytes_ref(b"synthetic firmware identity only")},
             "gateware": None, "persona": persona, "channels": channels,
             "clock": {"id": "synthetic.device.clock", "tick_hz": 1000000,
                       "timestamp_event": "instrument_report_receipt", "sync_reference": None, "uncertainty_ns": None},
             "configuration": {"revision": 1, "enabled_channels": [x["id"] for x in channels],
                               "sample_rate_hz": {x["id"]: None for x in channels},
                               "ranges": {x["id"]: None for x in channels}},
             "capabilities": ["READ", "CONFIGURE", "CALIBRATE", "UPDATE"], "control": "NONE"}
    return c.manifest(value)


def example_observations(device, *, boot_id="synthetic.boot.1", rounds=3):
    c.integer(rounds, minimum=1, maximum=32)
    context = dict.fromkeys(c.CONTEXT_FIELDS)
    context.update(asset_id="synthetic.asset.1", part_id="synthetic.part.1", part_revision="A",
                   lot_id="synthetic.lot.1", cavity_id="synthetic.cavity.1", cycle_id="synthetic.cycle.1",
                   serial_id="synthetic.serial.1", work_order_id="synthetic.order.1")
    if device["persona"] == "FieldLink":
        context["coordinate_reference"] = "synthetic.unsurveyed.local-frame"
    output = []
    for round_index in range(rounds):
        for i, (_, unit, value) in enumerate(_PROFILES[device["persona"]]):
            raw = f"{value + round_index * 0.001:.6f} {unit}\r\n".encode("ascii")
            output.append(c.make_observation(device, boot_id=boot_id, sequence=len(output),
                channel_id=f"CH{i}", raw=raw, tick=1000000 + round_index * 10000,
                origin="synthetic", context=context))
    return output


# Selectors belong to the application, never to an executable plugin loader.
# Historical V1 persona strings remain untouched inside original manifests.
_SELECTORS = {
    "gauge": "GaugeLink", "machine": "MachineLink", "condition": "ConditionLink",
    "field": "FieldLink", "thermal": "ThermalLink", "daq": "DAQLink",
    "calibration": "CalLink", "pressure": "PressureLink", "vibration": "VibrationLink",
    "electrical": "ElectricalLink",
}
_ALIASES = {alias: name for name, alias in _SELECTORS.items()}
_NEW_EXAMPLES = {
    "pressure": ("MachineLink", (("pressure", "Pa", 1200000.0),)),
    "vibration": ("ConditionLink", (("acceleration", "m/s^2", 0.125),)),
    "electrical": ("DAQLink", (("voltage", "V", 1.25),)),
}
# Deliberately finite semantic signatures. No implicit units, decoder installation,
# waveform support, calibration, sample-rate or machine-health inference.
_QUANTITY_PROFILES = {
    "gauge": {"length": ("m", "mm"), "force": ("N",), "mass": ("kg", "g")},
    "thermal": {"temperature": ("K", "degC"), "temperature_difference": ("K", "degC")},
    "pressure": {"pressure": ("Pa", "kPa", "MPa", "bar")},
    "vibration": {"acceleration": ("m/s^2",)},
    "electrical": {"voltage": ("V",), "current": ("A",)},
}


def resolve_profile(selector):
    """Return an explicit canonical selector, preserving legacy CLI aliases."""
    c.text(selector)
    name = _ALIASES.get(selector, selector)
    if name not in _SELECTORS:
        raise ValueError("Unknown Link profile; no automatic driver or profile discovery")
    return name


def selectors():
    return tuple(_SELECTORS) + tuple(_ALIASES)


def profile_examples(selector, *, boot_id="synthetic.boot.1", rounds=3):
    """Synthetic records through the SAME V1 contract, never a new device runtime.

    Legacy selectors produce byte-identical manifests/observations. Newly named
    pressure/vibration/electrical examples use a legacy wire compatibility tag;
    the tag is not a capability gate and never replaces an original manifest.
    """
    name = resolve_profile(selector)
    if name not in _NEW_EXAMPLES:
        device = example_manifest(_SELECTORS[name])
        return device, example_observations(device, boot_id=boot_id, rounds=rounds)
    c.integer(rounds, minimum=1, maximum=32)
    persona, specification = _NEW_EXAMPLES[name]
    device = example_manifest(persona)
    device["device_id"] = "synthetic.Link." + name + ".1"
    device["channels"] = [{"id": f"CH{i}", "transducer_id": f"synthetic.sensor.{i}",
                           "quantity": quantity, "unit": unit,
                           "frame": "synthetic.instrument.frame", "encoding": "notation-ascii-scalar.v1"}
                          for i, (quantity, unit, _) in enumerate(specification)]
    ids = [channel["id"] for channel in device["channels"]]
    device["configuration"].update(enabled_channels=ids, sample_rate_hz=dict.fromkeys(ids), ranges=dict.fromkeys(ids))
    device = c.manifest(device)
    values = []
    for step in range(rounds):
        for i, (_, unit, value) in enumerate(specification):
            values.append(c.make_observation(device, boot_id=boot_id, sequence=len(values),
                channel_id=ids[i], raw=f"{value + step * 0.001:.6f} {unit}\r\n".encode("ascii"),
                tick=1000000 + step * 10000, origin="synthetic"))
    return device, values


def unified_catalog():
    """Additive presentation over the historical catalogue, not a new registry."""
    value = catalog()
    value.update(application="Link", runtime="ciw.link_runtime.DeviceRuntime",
        profile_selectors=[{"id": name, "label": name.capitalize(), "legacy_alias": alias,
                            "example": "synthetic_only", "execution_authority": False}
                           for name, alias in _SELECTORS.items()],
        quantity_profiles=[{"id": name, "any_of": [{"quantity": q, "units": list(units)}
                                                   for q, units in signatures.items()],
                            "match_scope": "declared_channel_signature_only"}
                           for name, signatures in _QUANTITY_PROFILES.items()],
        physical_driver_support="not_established", composition="multiple_profiles_per_device",
        historical_personas="wire_compatibility_metadata_not_capability_or_authority")
    return value


def manifest_view(raw, *, expected_ref):
    """Typed finite sets and relations from one exact, operator-selected manifest.

    Matching is declaration-based and three-valued. UNKNOWN is not false evidence
    of physical incompatibility. No device is opened, registered or authenticated.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise ValueError("Require 1..65536 original manifest bytes")
    c.content_ref(expected_ref)
    if c.bytes_ref(raw) != expected_ref:
        raise ValueError("Link manifest differs from its externally selected bytes")
    device = c.manifest(c.loads(raw, 65536))
    from .operations.runner import seal
    def record(kind, **fields):
        return seal({"schema": "ciw." + kind + ".v1", **fields})
    def entity(kind, **identity):
        return {"kind": kind, "ref": c.content_identity({"kind": kind, **identity}), **identity}
    dev = entity("device", device_id=device["device_id"])
    instrument = entity("instrument", device_id=device["device_id"], instrument_id=device["instrument_id"])
    channels, relations, matches = [], [], []
    for channel in device["channels"]:
        item = entity("channel", device_id=device["device_id"], channel_id=channel["id"])
        enabled = channel["id"] in device["configuration"]["enabled_channels"]
        channels.append({**item, "transducer_id": channel["transducer_id"],
                         "quantity": channel["quantity"], "unit": channel["unit"],
                         "frame": channel["frame"], "encoding": channel["encoding"], "enabled": enabled})
        for predicate, source, target in (
            ("declares_channel", dev["ref"], item["ref"]),
            ("channel_of_instrument", item["ref"], instrument["ref"]),
            ("measures", item["ref"], entity("quantity", quantity=channel["quantity"])["ref"]),
            ("expressed_in", item["ref"], entity("unit", unit=channel["unit"])["ref"]),
        ):
            relations.append({"predicate": predicate, "source": source, "target": target,
                              "basis": "manifest_declaration"})
        for name, signatures in _QUANTITY_PROFILES.items():
            units = signatures.get(channel["quantity"])
            state = ("NO_MATCH" if units is None or not enabled else
                     "UNKNOWN" if channel["unit"] not in units else "DECLARED_MATCH")
            reason = ("channel_disabled" if not enabled else "quantity_not_in_profile" if units is None else
                      "unit_semantics_not_registered" if state == "UNKNOWN" else "explicit_quantity_and_unit")
            matches.append({"profile": name, "channel_ref": item["ref"], "status": state, "reason": reason,
                "scalar_interpretation": "requires_separate_decoder" if channel["encoding"] == "opaque.v1" else "declared_grammar",
                "execution_authority": False})
    return record("link-profile-view", application="Link", manifest_bytes_ref=expected_ref,
        manifest_ref=c.content_identity(device), legacy_persona=device["persona"],
        sets={"devices": [dev], "instruments": [instrument], "channels": channels,
              "quantities": [entity("quantity", quantity=q) for q in sorted({x["quantity"] for x in channels})],
              "units": [entity("unit", unit=u) for u in sorted({x["unit"] for x in channels})]},
        relations=relations, profile_matches=matches,
        qualification={"connected": "UNKNOWN", "device_authenticated": "UNKNOWN",
                       "calibration": "UNKNOWN", "physical_qualification": "NOT_ESTABLISHED"},
        authority={"execute": False, "configure": False, "machine_control": False,
                   "part_release": False, "state_admission": False},
        scope="typed_declaration_view_not_live_discovery_or_board_composition")
