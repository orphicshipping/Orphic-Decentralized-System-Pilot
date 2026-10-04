"""Bounded observations of a local session endpoint; never real-time qualification."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
from urllib.parse import urlsplit

from websockets.exceptions import WebSocketException

from .cli import request_remote
from .session import loads_json


SCHEMA = "ciw.transport-timing.v1"
DEFAULT_URL = "ws://127.0.0.1:8765"
QUANTITY = "fresh_connection_session_get_elapsed_ms"
SCOPE = (
    "Client monotonic elapsed time from invoking session.get through response validation "
    "and connection cleanup, including connection setup, serialization, network, server "
    "queueing and work, response parsing, and teardown. Each sample opens a fresh connection."
)
LIMITATIONS = [
    "Finite observations do not establish worst-case latency or hard real-time qualification.",
    "Client request timeout does not cancel or terminate server computation.",
    "Successful-only latency metrics exclude failed samples; every failure is retained and counted.",
    "A changed session, run, or evidence identity invalidates campaign latency aggregation.",
    "No authentication, remote multi-user isolation, physical validation, or equipment permission is established.",
    "Configured request deadlines exclude scheduler delays and cancellation or connection cleanup overhead.",
]
_ERRORS = {"timeout", "invalid_response", "transport_error"}
_ROOT_KEYS = {
    "schema", "started_at", "completed_at", "endpoint", "operation", "quantity", "scope",
    "parameters", "host", "samples", "summary", "hard_real_time_qualified", "limitations", "record_digest",
}


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _finite(value, name, *, positive=False):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite {'positive' if positive else 'nonnegative'} number")
    return value


def validate_endpoint(url):
    """Only explicit loopback endpoints, never credentials, queries, or fragments."""
    if (not isinstance(url, str) or len(url) > 1024 or any(ord(c) <= 32 or ord(c) >= 127 for c in url)
            or "?" in url or "#" in url):
        raise ValueError("Use a loopback WebSocket URL without credentials, query, or fragment")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"ws", "wss"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in {"", "/"}):
            raise ValueError
        if parsed.port is not None and parsed.port == 0:
            raise ValueError
        if parsed.hostname != "localhost" and not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Use a loopback WebSocket URL without credentials, query, or fragment") from exc
    return url


def _parameters(samples, timeout_s, budget_ms):
    if type(samples) is not int or not 1 <= samples <= 100:
        raise ValueError("samples must be an integer from 1 to 100")
    _finite(timeout_s, "timeout", positive=True)
    if timeout_s > 30 or samples * timeout_s > 120:
        raise ValueError("timeout must be at most 30 seconds and samples * timeout at most 120 seconds")
    if budget_ms is not None:
        _finite(budget_ms, "budget_ms", positive=True)
    return {"samples": samples, "timeout_s": timeout_s, "budget_ms": budget_ms,
            "configured_total_request_timeout_s": samples * timeout_s}


def _identity(response):
    """Validate the fixed session response sufficiently to identify an observation."""
    if (not isinstance(response, dict) or type(response.get("protocol_version")) is not int
            or response["protocol_version"] != 1 or response.get("type") != "response"
            or not isinstance(response.get("request_id"), str) or not response["request_id"]):
        raise ValueError("Invalid session response envelope")
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Invalid session snapshot")
    run, selection = payload.get("run"), payload.get("selection")
    if (not isinstance(run, dict) or not isinstance(selection, dict)
            or not isinstance(payload.get("results"), list)
            or not isinstance(run.get("channels"), dict) or not run["channels"]
            or not isinstance(run.get("metadata"), dict)):
        raise ValueError("Invalid session snapshot")
    identity = {"session_id": payload.get("session_id"), "run_id": run.get("run_id"),
                "evidence_id": run.get("evidence_id")}
    if any(not isinstance(value, str) or not value or len(value) > 512 for value in identity.values()):
        raise ValueError("Invalid session identity")
    if (not isinstance(run.get("instrument"), str) or not run["instrument"]
            or selection.get("run_id") != identity["run_id"]
            or type(selection.get("revision")) is not int or selection["revision"] < 0
            or not isinstance(selection.get("channel"), str) or selection["channel"] not in run["channels"]):
        raise ValueError("Invalid session selection")
    return identity


def _summary(rows, budget_ms):
    successful = [row for row in rows if row["status"] == "success"]
    identities = []
    for row in successful:
        if row["identity"] not in identities:
            identities.append(row["identity"])
    stable = len(identities) <= 1
    failures = len(rows) - len(successful)
    exceeded = sum(row["budget_exceeded"] is True for row in rows)
    valid = bool(successful) and stable
    metrics = None
    if valid:
        values = sorted(row["elapsed_ms"] for row in successful)
        metrics = {"count": len(values), "minimum_ms": values[0], "p50_ms": values[math.ceil(.5 * len(values)) - 1],
                   "p95_ms": values[math.ceil(.95 * len(values)) - 1], "maximum_ms": values[-1]}
    status = ("invalid_identity_change" if not stable else "incomplete" if failures else
              "observed_budget_exceeded" if exceeded else "observed_within_budget" if budget_ms is not None
              else "observed")
    return {"status": status, "attempted": len(rows), "successful": len(successful), "failed": failures,
            "timeout_count": sum(row["error"] == "timeout" for row in rows),
            "observed_budget_exceedances": exceeded,
            "successful_budget_exceedances": sum(row["budget_exceeded"] is True for row in successful),
            "identity_stable": stable if successful else None, "identities": identities,
            "aggregation_valid": valid, "metrics_population": "successful_samples_only",
            "percentile_method": "nearest_rank", "latency_ms": metrics}


def _digest(report):
    content = {key: value for key, value in report.items() if key != "record_digest"}
    return "sha256:" + hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"),
                                                 allow_nan=False).encode("utf-8")).hexdigest()


async def measure_transport(url=DEFAULT_URL, *, samples=5, timeout_s=3, budget_ms=None):
    """Measure only session.get, retaining failures without retrying or server writes."""
    endpoint = validate_endpoint(url)
    parameters = _parameters(samples, timeout_s, budget_ms)
    rows = []
    started_at = _utc_now()
    for index in range(samples):
        started = time.perf_counter_ns()
        identity = None
        error = None
        try:
            response = await request_remote(endpoint, "session.get", {}, timeout_s=timeout_s)
            identity = _identity(response)
        except TimeoutError:
            error = "timeout"
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            error = "invalid_response"
        except (OSError, RuntimeError, WebSocketException):
            # Exception text may contain endpoint/server data. Retain a stable classification only.
            error = "transport_error"
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        rows.append({"index": index + 1, "status": "success" if error is None else "failed",
                     "elapsed_ms": elapsed_ms, "identity": identity, "error": error,
                     "budget_exceeded": None if budget_ms is None else elapsed_ms > budget_ms})
    clock = time.get_clock_info("perf_counter")
    report = {"schema": SCHEMA, "started_at": started_at, "completed_at": _utc_now(), "endpoint": endpoint,
              "operation": "session.get", "quantity": QUANTITY, "scope": SCOPE, "parameters": parameters,
              "host": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
                       "python": platform.python_version(), "clock": "perf_counter_ns",
                       "clock_resolution_s": clock.resolution, "clock_monotonic": clock.monotonic},
              "samples": rows, "summary": _summary(rows, budget_ms), "hard_real_time_qualified": False,
              "limitations": list(LIMITATIONS)}
    report["record_digest"] = _digest(report)
    return validate_report(report)


def validate_report(report):
    """Check retained structure and recompute aggregates; a digest is not attestation."""
    if not isinstance(report, dict) or report.keys() != _ROOT_KEYS:
        raise ValueError("Invalid timing report fields")
    if (report["schema"] != SCHEMA or report["operation"] != "session.get" or report["quantity"] != QUANTITY
            or report["scope"] != SCOPE or report["hard_real_time_qualified"] is not False
            or report["limitations"] != LIMITATIONS):
        raise ValueError("Invalid timing report scope")
    validate_endpoint(report["endpoint"])
    for key in ("started_at", "completed_at"):
        if not isinstance(report[key], str):
            raise ValueError("Invalid timing report timestamp")
        try:
            stamp = datetime.fromisoformat(report[key])
        except ValueError as exc:
            raise ValueError("Invalid timing report timestamp") from exc
        if stamp.utcoffset() is None:
            raise ValueError("Timing report timestamps need a timezone")
    # Wall-clock adjustment may move completion time backwards; samples use a monotonic clock.
    parameters = report["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != {"samples", "timeout_s", "budget_ms", "configured_total_request_timeout_s"}:
        raise ValueError("Invalid timing report parameters")
    expected_parameters = _parameters(parameters["samples"], parameters["timeout_s"], parameters["budget_ms"])
    if json.dumps(parameters, sort_keys=True, allow_nan=False) != json.dumps(expected_parameters, sort_keys=True, allow_nan=False):
        raise ValueError("Inconsistent timing report parameters")
    host = report["host"]
    if (not isinstance(host, dict) or set(host) != {"system", "release", "machine", "python", "clock", "clock_resolution_s", "clock_monotonic"}
            or any(not isinstance(host[key], str) or not host[key] or len(host[key]) > 512 for key in ("system", "release", "machine", "python"))
            or host["clock"] != "perf_counter_ns" or host["clock_monotonic"] is not True):
        raise ValueError("Invalid timing report host metadata")
    _finite(host["clock_resolution_s"], "clock resolution", positive=True)
    rows = report["samples"]
    if not isinstance(rows, list) or len(rows) != parameters["samples"]:
        raise ValueError("Invalid timing report sample count")
    for index, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or set(row) != {"index", "status", "elapsed_ms", "identity", "error", "budget_exceeded"}
                or type(row["index"]) is not int or row["index"] != index):
            raise ValueError("Invalid timing report sample")
        _finite(row["elapsed_ms"], "elapsed_ms")
        expected_budget = None if parameters["budget_ms"] is None else row["elapsed_ms"] > parameters["budget_ms"]
        if row["budget_exceeded"] is not expected_budget:
            raise ValueError("Inconsistent timing budget observation")
        if row["status"] == "success":
            identity = row["identity"]
            if (row["error"] is not None or not isinstance(identity, dict) or set(identity) != {"session_id", "run_id", "evidence_id"}
                    or any(not isinstance(value, str) or not value or len(value) > 512 for value in identity.values())):
                raise ValueError("Invalid successful sample identity")
        elif row["status"] != "failed" or row["error"] not in _ERRORS or row["identity"] is not None:
            raise ValueError("Invalid failed sample")
    expected = _summary(rows, parameters["budget_ms"])
    # JSON comparison keeps boolean/int substitutions from passing Python's equality rules.
    if json.dumps(report["summary"], sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
        raise ValueError("Inconsistent timing report summary")
    if report["record_digest"] != _digest(report):
        raise ValueError("Timing report digest mismatch")
    return report


def write_report(path, report):
    """Atomically publish a complete, new report without replacing any existing path."""
    validate_report(report)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(report, indent=2, allow_nan=False) + "\n"
    with tempfile.TemporaryDirectory(prefix=".ciw-timing-", dir=path.parent) as staging:
        staged = Path(staging) / "report.json"
        with staged.open("w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ciw timing", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Observe bounded loopback session.get elapsed times")
    run.add_argument("--url", default=DEFAULT_URL)
    run.add_argument("--samples", type=int, default=5)
    run.add_argument("--timeout", type=float, default=3, help="Per-request client deadline in seconds")
    run.add_argument("--budget-ms", type=float, help="Optional observed elapsed-time threshold, not a guarantee")
    run.add_argument("--output", type=Path, required=True, help="New JSON report path; never overwrite")
    inspect = commands.add_parser("inspect", help="Check retained report and recompute its summary without contacting server")
    inspect.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            if os.path.lexists(args.output):
                raise ValueError("Use a new report path to preserve previous observations")
            report = asyncio.run(measure_transport(args.url, samples=args.samples, timeout_s=args.timeout, budget_ms=args.budget_ms))
            write_report(args.output, report)
            print(json.dumps({"report_file": str(args.output), "summary": report["summary"],
                              "hard_real_time_qualified": False}, indent=2, allow_nan=False))
            return 0 if report["summary"]["status"] in {"observed", "observed_within_budget"} else 1
        with args.path.open("rb") as stream:
            content = stream.read(1_000_001)
        if len(content) > 1_000_000:
            raise ValueError("Timing report exceeds the one-megabyte inspection limit")
        report = validate_report(loads_json(content.decode("utf-8")))
        print(json.dumps(report, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        # Do not reflect untrusted URL input or arbitrary endpoint exception messages.
        print(f"ciw timing: {type(exc).__name__}: unable to complete timing command", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
