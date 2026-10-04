"""Local bench captures, exact-byte imports, and data-only maintenance plans.

Content checks do not authenticate an operator, device, or imported measurement.
No function executes sanitization, firmware changes, or provisioning.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import threading
from urllib.parse import urlsplit

from . import hardware_contract as hardware
from .control_contracts import bytes_ref, content_ref, detached, keys, number, text
from .operations.runner import check_seal, digest, seal

MAX_RAW = 32768
EVIDENCE_SCHEMA = "ciw.hardware-bench-evidence.v1"
INVENTORY_SCHEMA = "ciw.hardware-bench-inventory.v1"
MAINTENANCE_SCHEMA = "ciw.hardware-maintenance-plan.v1"
RECEIPT_SCHEMA = "ciw.hardware-maintenance-evidence.v1"
DIAGNOSTIC_SCHEMA = "ciw.bench-diagnostic.v1"
PUBLIC_REVISION = "6274decc61212cc61e58796cc3b8cf5cfa035809"
PUBLIC_SHA256 = "sha256:2fd5253bb0792ac6472db1add36feb757218b8d42dee40d60752cfa73d6b68cf"
PUBLIC_PATH = "testdata/INTEL_SSDPE2KX080T8_1.json"
PUBLIC_URL = "https://raw.githubusercontent.com/prometheus-community/smartctl_exporter/" + PUBLIC_REVISION + "/" + PUBLIC_PATH
AUTHORITY = dict(hardware.AUTHORITY, maintenance_execution="not_performed", calibration="not_established")
PRECONDITIONS = ("owner_authorization", "target_identity_confirmation", "data_retention_decision",
                 "backup_verification", "power_and_recovery_readiness")


def _timestamp(value):
    text(value)
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Require a timezone-aware timestamp")
    return result


def _json(raw):
    if type(raw) is not bytes or not raw or len(raw) > MAX_RAW:
        raise ValueError("Require nonempty JSON bytes no larger than 32768 bytes")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result

    result = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs)
    if type(result) is not dict:
        raise ValueError("Require a JSON object")
    return detached(result)


def _target(configuration, slot, source_kind="operator_import"):
    configuration = hardware.validate_configuration(configuration)
    text(slot)
    matches = [item for item in configuration["components"] if item["slot"] == slot]
    if len(matches) != 1:
        raise ValueError("Target component slot is absent")
    component = matches[0]
    serial = component["serial_number"]
    text(serial)
    if source_kind == "operator_import" and (serial.strip().upper() in {"REDACTED", "UNKNOWN", "N/A", "NONE"}
                                            or serial.startswith("NET-SYNTHETIC-")):
        raise ValueError("Operator evidence requires a known target serial, not a fixture identifier")
    return {"slot": slot, "model": component["model"], "serial_number": serial}


def _origin(source_kind, provenance, raw):
    if source_kind not in {"operator_import", "external_public", "synthetic"}:
        raise ValueError("Unsupported bench source kind")
    if source_kind == "operator_import":
        if provenance is not None or bytes_ref(raw) == PUBLIC_SHA256:
            raise ValueError("A public fixture cannot be relabelled as operator acquisition")
        return None
    provenance = detached(provenance)
    keys(provenance, {"source", "derivation"})
    source = provenance["source"]
    keys(source, {"url", "revision", "license", "raw_sha256", "attribution"})
    for name in ("url", "revision", "license", "attribution"):
        text(source[name])
    url = urlsplit(source["url"])
    if url.scheme != "https" or not url.netloc or url.username or url.password:
        raise ValueError("Require an attributed HTTPS source URL")
    if re.fullmatch(r"[0-9a-f]{40}", source["revision"]) is None:
        raise ValueError("Require an immutable source commit")
    content_ref(source["raw_sha256"])
    if source_kind == "external_public":
        if source["raw_sha256"] != bytes_ref(raw) or provenance["derivation"] is not None:
            raise ValueError("Public record must retain exact source bytes")
    else:
        text(provenance["derivation"])
    return provenance


def _metric(name, unit, value, *, error=False):
    if value is not None:
        number(value)
    return {"metric_id": name, "unit": unit, "value": None if error else value,
            "status": "error" if error else "unavailable" if value is None else "available"}


def _smart(document):
    tool = document.get("smartctl")
    if type(tool) is not dict or type(tool.get("version")) is not list or not tool["version"]:
        raise ValueError("SMART JSON must identify its smartctl version")
    if len(tool["version"]) > 4 or any(type(item) is not int or not 0 <= item <= 999 for item in tool["version"]):
        raise ValueError("Invalid smartctl version")
    exit_status = tool.get("exit_status")
    if type(exit_status) is not int or not 0 <= exit_status <= 255:
        raise ValueError("SMART JSON must retain its integer exit status")
    failed = bool(exit_status & 7)
    for field in ("smart_status", "temperature", "power_on_time"):
        if field in document and type(document[field]) is not dict:
            raise ValueError("SMART nested fields must be objects")
    health = document.get("smart_status", {}).get("passed")
    if health is not None and type(health) is not bool:
        raise ValueError("SMART health must be a boolean when reported")
    nvme = document.get("nvme_smart_health_information_log", {})
    if type(nvme) is not dict:
        raise ValueError("Invalid NVMe health log")
    fields = [("smart-health", "boolean", None if health is None else int(health)),
              ("temperature", "degC", document.get("temperature", {}).get("current")),
              ("power-on-hours", "h", document.get("power_on_time", {}).get("hours")),
              ("power-cycles", "count", document.get("power_cycle_count")),
              ("nvme-media-errors", "count", nvme.get("media_errors")),
              ("nvme-critical-warning", "bitfield", nvme.get("critical_warning")),
              ("nvme-available-spare", "%", nvme.get("available_spare")),
              ("nvme-percentage-used", "%", nvme.get("percentage_used"))]
    # Vendor-specific ATA raw counters are deliberately not interpreted here.
    return ({"model": document.get("model_name"), "serial_number": document.get("serial_number")},
            {"name": "smartctl", "version": ".".join(map(str, tool["version"]))},
            [_metric(name, unit, value, error=failed) for name, unit, value in fields])


def _diagnostic(document, procedure_id, duration_s):
    keys(document, {"schema", "device", "tool", "procedure_id", "duration_s", "metrics"})
    if document["schema"] != DIAGNOSTIC_SCHEMA:
        raise ValueError("Unsupported diagnostic JSON schema")
    keys(document["device"], {"model", "serial_number"})
    keys(document["tool"], {"name", "version"})
    for value in document["tool"].values():
        text(value)
    if number(document["duration_s"]) < 0:
        raise ValueError("Diagnostic duration must be nonnegative")
    if document["procedure_id"] != procedure_id or document["duration_s"] != duration_s:
        raise ValueError("Diagnostic procedure or duration differs from import declaration")
    metrics = document["metrics"]
    if type(metrics) is not list or not 1 <= len(metrics) <= 64:
        raise ValueError("Require 1 to 64 diagnostic metrics")
    seen = set()
    for metric in metrics:
        keys(metric, {"metric_id", "unit", "value", "status"})
        text(metric["metric_id"]); text(metric["unit"])
        if metric["metric_id"] in seen:
            raise ValueError("Duplicate diagnostic metric")
        seen.add(metric["metric_id"])
        if metric["status"] not in {"available", "unavailable", "error"}:
            raise ValueError("Unsupported diagnostic metric status")
        if metric["status"] == "available":
            number(metric["value"])
        elif metric["value"] is not None:
            raise ValueError("Unavailable/error metrics must have null values")
    return document["device"], document["tool"], metrics


def import_diagnostic(raw_bytes, configuration, component_slot, *, format="smartctl-json", operator,
                      captured_at, procedure_id, duration_s=0, source_kind="operator_import", provenance=None):
    """Bind exact imported bytes to a declared device; never authenticate them."""
    configuration = hardware.validate_configuration(configuration)
    target = _target(configuration, component_slot, source_kind)
    text(operator); text(procedure_id); _timestamp(captured_at)
    if number(duration_s) < 0:
        raise ValueError("Duration must be nonnegative")
    document = _json(raw_bytes)
    provenance = _origin(source_kind, provenance, raw_bytes)
    if format == "smartctl-json":
        if duration_s != 0:
            raise ValueError("A SMART snapshot is not a sustained-duration test")
        device, tool, metrics = _smart(document)
    elif format == "diagnostic-json":
        device, tool, metrics = _diagnostic(document, procedure_id, duration_s)
    else:
        raise ValueError("Unsupported diagnostic format")
    if device != {key: target[key] for key in ("model", "serial_number")}:
        raise ValueError("Imported device identity differs from the target component")
    return seal({"schema": EVIDENCE_SCHEMA, "asset_id": configuration["asset_id"],
                 "configuration_ref": hardware.configuration_ref(configuration), "target": target,
                 "source_kind": source_kind, "provenance": provenance, "format": format,
                 "operator": operator, "captured_at": captured_at, "procedure_id": procedure_id,
                 "duration_s": duration_s, "tool": tool, "metrics": metrics,
                 "raw_sha256": bytes_ref(raw_bytes), "raw_base64": base64.b64encode(raw_bytes).decode("ascii"),
                 "authority": dict(AUTHORITY)})


def validate_evidence(record, configuration):
    record = detached(record)
    check_seal(record)
    raw = base64.b64decode(record["raw_base64"], validate=True)
    expected = import_diagnostic(raw, configuration, record["target"]["slot"],
        **{key: record[key] for key in ("format", "operator", "captured_at", "procedure_id", "duration_s",
                                      "source_kind", "provenance")})
    if digest(record) != digest(expected):
        raise ValueError("Bench evidence differs from its retained raw bytes or configuration")
    return record


def assessment_request(configuration, plan, evidence_records, mappings):
    """Map named metrics into the existing assessment contract without zero-fill."""
    configuration = hardware.validate_configuration(configuration)
    if type(evidence_records) is not list or not 1 <= len(evidence_records) <= 64:
        raise ValueError("Require 1 to 64 evidence records")
    evidence = {}
    for value in evidence_records:
        checked = validate_evidence(value, configuration)
        if checked["record_digest"] in evidence:
            raise ValueError("Duplicate evidence record")
        evidence[checked["record_digest"]] = checked
    if type(mappings) is not list or len(mappings) > 64:
        raise ValueError("Require bounded metric mappings")
    operators = {item["operator"] for item in evidence.values()}
    if len(operators) != 1:
        raise ValueError("One assessment import must declare one operator")
    records = []
    for mapping in mappings:
        keys(mapping, {"test_id", "evidence_ref", "metric_id"})
        item = evidence.get(mapping["evidence_ref"])
        if item is None:
            raise ValueError("Mapping references unattached evidence")
        metrics = {metric["metric_id"]: metric for metric in item["metrics"]}
        metric = metrics.get(mapping["metric_id"])
        if metric is None:
            raise ValueError("Mapping references an unknown metric")
        notes = json.dumps({"evidence_ref": item["record_digest"], "raw_sha256": item["raw_sha256"],
                            "metric_id": metric["metric_id"]}, sort_keys=True, separators=(",", ":"))
        records.append({"test_id": mapping["test_id"], "procedure_id": item["procedure_id"],
            "unit": metric["unit"], "value": metric["value"], "duration_s": item["duration_s"],
            "status": {"available": "completed", "unavailable": "not_run", "error": "error"}[metric["status"]],
            "notes": notes})
    captured = max((_timestamp(item["captured_at"]) for item in evidence.values())).astimezone(timezone.utc).isoformat()
    source_kind = "operator_import" if all(item["source_kind"] == "operator_import" for item in evidence.values()) else "synthetic"
    request = {"schema": hardware.REQUEST_SCHEMA, "configuration": configuration, "plan": detached(plan),
        "observations": {"schema": hardware.OBSERVATIONS_SCHEMA, "asset_id": configuration["asset_id"],
            "configuration_ref": hardware.configuration_ref(configuration), "source_kind": source_kind,
            "captured_at": captured, "operator": next(iter(operators)),
            "tool": {"name": "NET bench evidence mapper", "version": "1"},
            "environment": "Local imported records; source authenticity not established", "records": records}}
    return hardware.validate_request(request)


def validate_assessment_binding(request, evidence_records):
    request = hardware.validate_request(request)
    mappings = []
    for record in request["observations"]["records"]:
        notes = _json(record["notes"].encode("utf-8"))
        keys(notes, {"evidence_ref", "raw_sha256", "metric_id"})
        mappings.append({"test_id": record["test_id"], "evidence_ref": notes["evidence_ref"],
                         "metric_id": notes["metric_id"]})
    expected = assessment_request(request["configuration"], request["plan"], evidence_records, mappings)
    if digest(request) != digest(expected):
        raise ValueError("Assessment is not exactly derived from attached bench evidence")


def maintenance_plan(configuration, component_slot, *, action, expected_configuration,
                     procedure_id, operator, preconditions=None, source_kind="operator_import"):
    configuration = hardware.validate_configuration(configuration)
    expected = hardware.validate_configuration(expected_configuration)
    if action not in {"sanitize", "provision"} or source_kind not in {"operator_import", "synthetic"}:
        raise ValueError("Unsupported maintenance action or source kind")
    text(procedure_id); text(operator)
    target = _target(configuration, component_slot, source_kind)
    if expected["asset_id"] != configuration["asset_id"] or _target(expected, component_slot, source_kind) != target:
        raise ValueError("Maintenance plan cannot change its target device identity")
    if preconditions is None:
        preconditions = [{"id": name, "status": "pending", "evidence_ref": None} for name in PRECONDITIONS]
    preconditions = detached(preconditions)
    if type(preconditions) is not list or len(preconditions) != len(PRECONDITIONS):
        raise ValueError("Require all maintenance preconditions")
    for value, expected_id in zip(preconditions, PRECONDITIONS):
        keys(value, {"id", "status", "evidence_ref"})
        if value["id"] != expected_id or value["status"] not in {"pending", "declared_satisfied"}:
            raise ValueError("Invalid maintenance precondition")
        if value["status"] == "pending":
            if value["evidence_ref"] is not None:
                raise ValueError("Pending preconditions cannot claim evidence")
        else:
            content_ref(value["evidence_ref"])
    return seal({"schema": MAINTENANCE_SCHEMA, "asset_id": configuration["asset_id"],
        "configuration_ref": hardware.configuration_ref(configuration), "configuration": configuration,
        "expected_configuration": expected, "expected_configuration_ref": hardware.configuration_ref(expected),
        "source_kind": source_kind, "target": target, "action": action, "procedure_id": procedure_id,
        "operator": operator, "destructive_operation": True, "execution": "not_supported",
        "preconditions": preconditions, "verification_required": True, "authority": dict(AUTHORITY)})


def validate_maintenance_plan(plan):
    plan = detached(plan); check_seal(plan)
    expected = maintenance_plan(plan["configuration"], plan["target"]["slot"],
        **{key: plan[key] for key in ("action", "expected_configuration", "procedure_id", "operator", "preconditions", "source_kind")})
    if digest(plan) != digest(expected):
        raise ValueError("Maintenance plan fields or execution authority changed")
    return plan


def import_maintenance_result(raw_bytes, plan):
    """Verify imported binding declarations, never the physical erase/provisioning."""
    plan = validate_maintenance_plan(plan)
    value = _json(raw_bytes)
    keys(value, {"schema", "plan_ref", "target", "configuration_ref", "expected_configuration_ref",
                 "action", "procedure_id", "operator", "performed_at", "tool", "status", "verification"})
    if value["schema"] != "ciw.hardware-maintenance-result.v1" or value["plan_ref"] != plan["record_digest"]:
        raise ValueError("Maintenance result belongs to a different plan")
    for name in ("target", "configuration_ref", "expected_configuration_ref", "action", "procedure_id"):
        if value[name] != plan[name]:
            raise ValueError("Maintenance result target/configuration/procedure mismatch")
    text(value["operator"]); _timestamp(value["performed_at"])
    keys(value["tool"], {"name", "version"})
    for entry in value["tool"].values(): text(entry)
    if value["status"] not in {"completed", "failed", "not_run"}:
        raise ValueError("Unsupported imported maintenance status")
    keys(value["verification"], {"status", "evidence_ref"})
    if value["verification"]["status"] not in {"declared_pass", "declared_fail", "not_performed"}:
        raise ValueError("Unsupported declared verification status")
    if value["verification"]["status"] == "not_performed":
        if value["verification"]["evidence_ref"] is not None: raise ValueError("No verification was performed")
    else:
        content_ref(value["verification"]["evidence_ref"])
    return seal({"schema": RECEIPT_SCHEMA, "asset_id": plan["asset_id"],
        "configuration_ref": plan["configuration_ref"], "source_kind": plan["source_kind"],
        "plan_ref": plan["record_digest"], "binding_status": "consistent", "declared_result": value,
        "raw_sha256": bytes_ref(raw_bytes), "raw_base64": base64.b64encode(raw_bytes).decode("ascii"),
        "authority": dict(AUTHORITY)})


def validate_maintenance_result(record, plan):
    record = detached(record); check_seal(record)
    expected = import_maintenance_result(base64.b64decode(record["raw_base64"], validate=True), plan)
    if digest(record) != digest(expected): raise ValueError("Maintenance evidence differs from retained bytes")
    return record


_WINDOWS_INVENTORY = r'''
$ErrorActionPreference = 'Stop'
$PSModuleAutoLoadingPreference = 'None'
Import-Module ($PSHOME + '\Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
Import-Module ($PSHOME + '\Modules\CimCmdlets\CimCmdlets.psd1')
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$queries = @(
 @('system','Win32_ComputerSystem',@('Manufacturer','Model','TotalPhysicalMemory')),
 @('os','Win32_OperatingSystem',@('Caption','Version','OSArchitecture')),
 @('processors','Win32_Processor',@('Name','Manufacturer','NumberOfCores','NumberOfLogicalProcessors')),
 @('memory','Win32_PhysicalMemory',@('Manufacturer','PartNumber','SerialNumber','Capacity','Speed')),
 @('storage','Win32_DiskDrive',@('Index','Model','SerialNumber','FirmwareRevision','Size','InterfaceType'))
)
$result = @{}
foreach ($query in $queries) {
 try {
  $rows = @(CimCmdlets\Get-CimInstance -ClassName $query[1] | Select-Object -First 65 -Property $query[2])
  if ($rows.Count -gt 64) { $result[$query[0]] = @{status='error'; rows=@(); reason='row_limit'} }
  else { $result[$query[0]] = @{status='available'; rows=$rows} }
 } catch { $result[$query[0]] = @{status='error'; rows=@(); reason=$_.Exception.GetType().Name} }
}
$result | ConvertTo-Json -Depth 5 -Compress
'''


def _system_directory():
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    count = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not count or count >= len(buffer): raise OSError("Cannot resolve trusted Windows system directory")
    return Path(buffer.value)


def _trusted_tool(name):
    """Resolve only supported system locations, never PATH or saved tool paths."""
    if platform.system() == "Windows":
        if name == "powershell":
            root = _system_directory().resolve()
            candidate = root / "WindowsPowerShell/v1.0/powershell.exe"
        elif name == "smartctl":
            import ctypes
            buffer = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x26, None, 0, buffer) != 0: return None
            root = Path(buffer.value).resolve()
            candidate = root / "smartmontools/bin/smartctl.exe"
        else: return None
        if not candidate.is_file() or not candidate.resolve().is_relative_to(root): return None
        return candidate
    if platform.system() != "Linux" or name not in {"lsblk", "smartctl"}: return None
    candidates = (Path('/usr/bin/lsblk'), Path('/bin/lsblk')) if name == "lsblk" else (
        Path('/usr/sbin/smartctl'), Path('/usr/bin/smartctl'), Path('/sbin/smartctl'))
    for candidate in candidates:
        if not candidate.is_file(): continue
        resolved = candidate.resolve()
        info = resolved.stat()
        if (resolved.is_relative_to('/usr') or resolved.is_relative_to('/bin') or resolved.is_relative_to('/sbin')) and info.st_uid == 0 and not info.st_mode & 0o022:
            return resolved
    return None


def _bounded_command(command, *, timeout=20, maximum=MAX_RAW):
    """Fixed internal commands only; retain at most maximum bytes in memory."""
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}}
    environment.update({"LC_ALL": "C", "LANG": "C", "PATH": str(Path(command[0]).parent)})
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=environment, shell=False, creationflags=flags)
    except OSError as error:
        return {"status": "error", "reason": type(error).__name__, "returncode": None, "raw": b""}
    captured = bytearray(); limited = threading.Event()

    def read():
        while True:
            block = process.stdout.read(4096)
            if not block: break
            remaining = maximum - len(captured)
            captured.extend(block[:remaining])
            if len(block) > remaining:
                limited.set()
                try: process.kill()
                except OSError: pass
                break

    reader = threading.Thread(target=read, daemon=True); reader.start()
    reason = None
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        reason = "timeout"; process.kill(); process.wait(timeout=5)
    reader.join(timeout=5)
    if reader.is_alive(): reason = "incomplete_output"
    else: process.stdout.close()
    if limited.is_set(): reason = "output_limit"
    if reason is None and process.returncode: reason = "collector_exit_nonzero"
    return {"status": "available" if reason is None else "error", "reason": reason,
            "returncode": process.returncode, "raw": bytes(captured)}


def _field(value):
    if value is None or value == "": return {"status": "unavailable", "value": None}
    return {"status": "available", "value": value}


def _read_local(path, maximum=8192):
    try:
        with path.open('rb') as stream: raw = stream.read(maximum + 1)
        if len(raw) > maximum: return {"status": "error", "value": None, "reason": "output_limit"}
        value = raw.decode('utf-8').strip()
        return _field(value)
    except FileNotFoundError: return {"status": "unavailable", "value": None}
    except (OSError, UnicodeError) as error:
        return {"status": "error", "value": None, "reason": type(error).__name__}


def capture_inventory(asset_id, operator):
    """A low-impact local snapshot; the asset association is caller-declared."""
    text(asset_id); text(operator)
    system = platform.system()
    tool = _trusted_tool("powershell" if system == "Windows" else "lsblk")
    groups = {}; capture = {"status": "unavailable", "reason": "trusted_collector_unavailable", "returncode": None, "raw": b""}
    tool_identity = None
    if tool is not None:
        with tool.open('rb') as stream: tool_bytes = stream.read(32 * 1024 * 1024 + 1)
        if len(tool_bytes) > 32 * 1024 * 1024: raise ValueError("Collector executable exceeds identity bound")
        tool_identity = {"path": str(tool), "sha256": bytes_ref(tool_bytes)}
        if system == "Windows":
            encoded = base64.b64encode(_WINDOWS_INVENTORY.encode('utf-16le')).decode('ascii')
            command = [str(tool), '-NoProfile', '-NonInteractive', '-OutputFormat', 'Text', '-EncodedCommand', encoded]
        else:
            command = [str(tool), '--json', '--bytes', '--nodeps', '--output', 'NAME,PATH,TYPE,SIZE,MODEL,SERIAL,ROTA']
        capture = _bounded_command(command)
        if capture["status"] == "available":
            try:
                parsed = _json(capture["raw"])
                if system == "Windows":
                    for name, group in parsed.items():
                        if type(group.get("rows")) is not list or len(group["rows"]) > 64: raise ValueError("Unbounded collector rows")
                        if group["status"] not in {"available", "unavailable", "error"}:
                            raise ValueError("Unsupported collector status")
                        group_status = "unavailable" if group["status"] == "available" and not group["rows"] else group["status"]
                        groups[name] = {"status": group_status, "reason": group.get("reason"), "rows": [
                            {key: _field(value) for key, value in row.items()} for row in group["rows"]]}
                    if groups and all(group["status"] != "available" for group in groups.values()):
                        capture.update(status="error" if any(group["status"] == "error" for group in groups.values()) else "unavailable",
                                       reason="no_inventory_categories_available")
                else:
                    rows = parsed["blockdevices"]
                    if type(rows) is not list or len(rows) > 64: raise ValueError("Unbounded collector rows")
                    groups["storage"] = {"status": "available", "rows": [{key: _field(value) for key, value in row.items()} for row in rows]}
            except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
                capture.update(status="error", reason="invalid_collector_json")
    if system == "Linux":
        groups["system"] = {name: _read_local(Path('/sys/class/dmi/id') / field)
                            for name, field in (("manufacturer", "sys_vendor"), ("model", "product_name"), ("serial_number", "product_serial"))}
        groups["memory"] = {"proc_meminfo": _read_local(Path('/proc/meminfo'))}
        groups["processors"] = {"logical_cpu_count": _field(os.cpu_count())}
    return seal({"schema": INVENTORY_SCHEMA, "asset_id": asset_id, "operator": operator,
        "captured_at": datetime.now(timezone.utc).isoformat(), "platform": system,
        "source_kind": "operator_import", "asset_association": "caller_declared_not_verified",
        "collector": "net.local-inventory.v1", "tool": tool_identity, "groups": groups,
        "status": capture["status"], "reason": capture["reason"], "returncode": capture["returncode"],
        "raw_sha256": bytes_ref(capture["raw"]), "raw_base64": base64.b64encode(capture["raw"]).decode('ascii'),
        "sensitive_identifiers": "local_only", "authority": dict(AUTHORITY)})


def public_source():
    return {"url": PUBLIC_URL, "revision": PUBLIC_REVISION, "license": "Apache-2.0",
            "raw_sha256": PUBLIC_SHA256, "attribution": "Prometheus smartctl_exporter contributors; upstream test fixture"}


def example_bundle(case="pass"):
    """Deterministic examples derived from a vendored public test fixture."""
    if case not in {"public", "pass", "fail", "missing", "stale"}: raise ValueError("Unknown bench example case")
    raw = (Path(__file__).parent / 'bench_examples/smartctl-public.json').read_bytes()
    if bytes_ref(raw) != PUBLIC_SHA256: raise ValueError("Bundled public source bytes changed")
    document = _json(raw)
    source_kind = "external_public" if case == "public" else "synthetic"
    provenance = {"source": public_source(), "derivation": None}
    if case != "public":
        provenance["derivation"] = "net.bench.example.v1:" + case + "; synthetic identity and declared counterexample; not calibrated or statistically representative"
        document['serial_number'] = 'NET-SYNTHETIC-001'
        if case == "fail":
            document['nvme_smart_health_information_log']['media_errors'] = 5
            document['smart_status']['passed'] = False
            document['smartctl']['exit_status'] = 8
        elif case == "missing":
            del document['nvme_smart_health_information_log']['media_errors']
        raw = (json.dumps(document, sort_keys=True, indent=2) + '\n').encode('utf-8')
    configuration = {"schema": hardware.CONFIGURATION_SCHEMA, "asset_id": "NET-BENCH-DEMO", "revision": "1",
        "manufacturer": "Example only", "model": "Public-fixture-derived workstation", "serial_number": None,
        "components": [{"slot": "storage-0", "kind": "storage", "manufacturer": "Intel",
                        "model": document['model_name'], "serial_number": document['serial_number'],
                        "firmware": document.get('firmware_version')}], "software": []}
    evidence = import_diagnostic(raw, configuration, 'storage-0', source_kind=source_kind, provenance=provenance,
        operator='NET offline demonstration', captured_at='2009-02-13T23:31:30+00:00',
        procedure_id='net.smart-snapshot.v1')
    plan = {"schema": hardware.PLAN_SCHEMA, "plan_id": "synthetic-storage-example", "revision": "1",
        "purpose": "Demonstration thresholds, not physical acceptance calibration", "checks": [
            {"test_id": "media-errors", "procedure_id": "net.smart-snapshot.v1", "unit": "count",
             "minimum": 0, "maximum": 0, "minimum_duration_s": 0},
            {"test_id": "smart-health", "procedure_id": "net.smart-snapshot.v1", "unit": "boolean",
             "minimum": 1, "maximum": 1, "minimum_duration_s": 0}]}
    mappings = [{"test_id": name, "metric_id": metric, "evidence_ref": evidence['record_digest']}
                for name, metric in (("media-errors", "nvme-media-errors"), ("smart-health", "smart-health"))]
    request = assessment_request(configuration, plan, [evidence], mappings)
    if case == "stale": request['configuration']['revision'] = '2'
    return {"case": case, "configuration": configuration, "evidence": evidence, "plan": plan,
            "mappings": mappings, "request": request}


def example_evidence(case="pass"):
    return example_bundle(case)["evidence"]


def example_request(case="pass"):
    return example_bundle(case)["request"]


def capture_smart(configuration, component_slot, device, operator):
    """Explicit-device, read-only SMART snapshot; never start self-tests or write."""
    configuration = hardware.validate_configuration(configuration)
    target = _target(configuration, component_slot)
    text(operator); text(device)
    pattern = r"/dev/(?:pd[0-9]{1,3}|sd[a-z]{1,2}|nvme[0-9]+(?:n[0-9]+)?)" if platform.system() == "Windows" else r"/dev/(?:sd[a-z]+|nvme[0-9]+(?:n[0-9]+)?)"
    if re.fullmatch(pattern, device) is None:
        raise ValueError("Require an explicit supported whole-device SMART path")
    tool = _trusted_tool("smartctl")
    capture = {"status": "unavailable", "reason": "trusted_smartctl_unavailable", "returncode": None, "raw": b""}
    identity = None; evidence = None
    if tool is not None:
        with tool.open('rb') as stream: tool_bytes = stream.read(32 * 1024 * 1024 + 1)
        if len(tool_bytes) > 32 * 1024 * 1024: raise ValueError("Collector executable exceeds identity bound")
        identity = {"path": str(tool), "sha256": bytes_ref(tool_bytes)}
        capture = _bounded_command([str(tool), '--json', '--info', '--health', '--attributes', '--nocheck=standby', device])
        if (capture['reason'] == 'collector_exit_nonzero' and type(capture['returncode']) is int
                and 0 <= capture['returncode'] <= 255 and not capture['returncode'] & 7):
            capture.update(status='available', reason=None)
        if capture['status'] == 'available':
            try:
                if _json(capture['raw'])['smartctl']['exit_status'] != capture['returncode']:
                    raise ValueError("SMART exit status differs from process result")
                evidence = import_diagnostic(capture['raw'], configuration, component_slot, operator=operator,
                    captured_at=datetime.now(timezone.utc).isoformat(), procedure_id='net.smart-snapshot.v1')
            except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
                capture.update(status='error', reason='invalid_or_mismatched_device_evidence')
    return seal({'schema': 'ciw.hardware-smart-capture.v1', 'asset_id': configuration['asset_id'],
        'configuration_ref': hardware.configuration_ref(configuration), 'target': target, 'device': device,
        'source_kind': 'operator_import', 'collector': 'net.smart-snapshot.v1', 'tool': identity,
        'status': capture['status'], 'reason': capture['reason'], 'returncode': capture['returncode'],
        'raw_sha256': bytes_ref(capture['raw']), 'raw_base64': base64.b64encode(capture['raw']).decode('ascii'),
        'evidence': evidence, 'authority': dict(AUTHORITY), 'sensitive_identifiers': 'local_only'})
