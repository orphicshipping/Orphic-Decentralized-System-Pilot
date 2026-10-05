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
