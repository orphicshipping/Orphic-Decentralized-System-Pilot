"""Bounded observations of a local session endpoint; never real-time qualification."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import errno
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
MAX_REPORT_BYTES = 4_000_000
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
        elif (row["status"] != "failed" or not isinstance(row["error"], str)
                or row["error"] not in _ERRORS or row["identity"] is not None):
            raise ValueError("Invalid failed sample")
    expected = _summary(rows, parameters["budget_ms"])
    # JSON comparison keeps boolean/int substitutions from passing Python's equality rules.
    if json.dumps(report["summary"], sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
        raise ValueError("Inconsistent timing report summary")
    if report["record_digest"] != _digest(report):
        raise ValueError("Timing report digest mismatch")
    return report


class ReportSizeError(ValueError):
    """The same byte limit applies to report publication and inspection."""


def write_report(path, report):
    """Atomically publish a complete, new report without replacing any existing path."""
    validate_report(report)
    content = (json.dumps(report, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(content) > MAX_REPORT_BYTES:
        raise ReportSizeError("Timing report exceeds the report byte limit")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ciw-timing-", dir=path.parent) as staging:
        staged = Path(staging) / "report.json"
        with staged.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, path)
    return path


def _print_summary(report, path, *, inspected=False):
    summary = report["summary"]
    print(f"Timing report: {path}")
    if inspected:
        print("Report integrity: valid (offline inspection; campaign outcome is shown below)")
    print(f"Campaign: {summary['status']}")
    print(f"Samples: {summary['attempted']} attempted; {summary['successful']} successful; "
          f"{summary['failed']} failed ({summary['timeout_count']} timeouts)")
    metrics = summary["latency_ms"]
    if metrics is not None:
        print(f"Successful-only latency ({metrics['count']} samples; nearest-rank percentiles): "
              f"p50 {metrics['p50_ms']:.3f} ms; p95 {metrics['p95_ms']:.3f} ms; "
              f"max {metrics['maximum_ms']:.3f} ms")
    elif summary["identity_stable"] is False:
        print("Successful-only latency: unavailable; session, run, or evidence identity changed")
    else:
        print("Successful-only latency: unavailable; no successful samples")
    budget = report["parameters"]["budget_ms"]
    if budget is None:
        print("Observed budget: not specified")
    else:
        print(f"Observed budget: {budget:g} ms; {summary['observed_budget_exceedances']} of "
              f"{summary['attempted']} attempts exceeded it "
              f"({summary['successful_budget_exceedances']} successful samples)")
    print("Scope: fresh-connection session.get elapsed time, including setup, validation, and cleanup")
    print("Hard real-time qualified: false. Finite observations do not guarantee latency.")
    if summary["failed"]:
        print("Failed samples remain in the saved report. Client timeout does not cancel server computation.")
    if summary["status"] == "incomplete":
        print("Next: check that the local session is running and inspect sample error codes before retrying.")
    elif summary["status"] == "invalid_identity_change":
        print("Next: use a stable session and rerun into a new report file.")
    elif summary["status"] == "observed_budget_exceeded":
        print("Next: review the retained slow samples and workload before setting an operational budget.")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally repeats invalid values, which may accidentally contain credentials.
        instructions = {
            "--samples": "--samples requires an integer from 1 to 100",
            "--timeout": "--timeout requires a finite positive number, at most 30 seconds",
            "--budget-ms": "--budget-ms requires a finite positive number",
            "--url": "--url requires a loopback WebSocket URL without credentials, query, or fragment",
            "--output": "--output requires a new report file path",
            "--summary": "--summary does not accept a value",
        }
        safe = "Invalid arguments; use --help for command usage and bounds"
        for option, instruction in instructions.items():
            if message.startswith(f"argument {option}:"):
                safe = instruction
                break
        if message.startswith("the following arguments are required:"):
            safe = message  # Names come from this parser's fixed command and option definitions.
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {safe}\n")


def _command_error(exc, phase):
    """Give useful local diagnostics without reflecting endpoint or file contents."""
    if phase == "parameters" and isinstance(exc, ValueError):
        return str(exc)  # Only the fixed messages of validate_endpoint/_parameters reach this phase.
    if isinstance(exc, FileExistsError):
        return "Report path already exists; choose a new --output path to preserve previous observations"
    if isinstance(exc, FileNotFoundError):
        return ("Report file was not found; check the path passed to inspect" if phase == "read" else
                "An output directory is unavailable; choose an existing writable directory")
    if isinstance(exc, (IsADirectoryError, NotADirectoryError)):
        return "Expected a report file path; check that parent paths are directories and the target is a file"
    if isinstance(exc, PermissionError):
        return ("Cannot read the report; check file permissions and that the path is a file" if phase == "read" else
                "Cannot create the report; choose a writable directory and a new output filename")
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "Not enough storage for the report; free space or choose another writable directory"
    if isinstance(exc, ReportSizeError):
        return f"Report exceeds the {MAX_REPORT_BYTES:,}-byte limit; use a bounded timing report"
    if phase == "decode":
        return "Report is not valid UTF-8 JSON; inspect the original timing report file"
    if phase == "validate":
        return "Report validation failed; its schema, samples, summary, or digest is inconsistent. Use the original report or rerun into a new file"
    if phase == "read":
        return "Unable to read the report; check the file path and access permissions"
    if phase == "write":
        return "Unable to publish the report; choose a writable directory supporting atomic hard-link creation and a new filename"
    return "Unable to create a valid timing report; check the local endpoint and retry with a new output path"


def main(argv=None):
    parser = _Parser(prog="ciw timing", description=__doc__, epilog=(
        "JSON is printed by default; --summary selects readable terminal output. "
        "Run exit codes: 0 = successful campaign within any supplied budget; "
        "1 = saved campaign has failed samples, budget breaches, or identity changes; 2 = command error. "
        "Inspect exits 0 for a structurally valid retained report, including a failed campaign; 2 otherwise."))
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Observe bounded loopback session.get elapsed times", epilog=(
        "Each sample opens a fresh connection. samples * timeout must be at most 120 seconds. "
        "Deadlines exclude scheduling and cleanup overhead. Exit 1 still saves the complete report; "
        "no result qualifies hard real-time operation."))
    run.add_argument("--url", default=DEFAULT_URL, help="Loopback ws(s):// endpoint, path / only; no credentials, query, or fragment (default: %(default)s)")
    run.add_argument("--samples", type=int, default=5, help="Number of attempts, 1 to 100 (default: %(default)s)")
    run.add_argument("--timeout", type=float, default=3, help="Finite positive per-request deadline, at most 30 seconds (default: %(default)s)")
    run.add_argument("--budget-ms", type=float, help="Finite positive observed elapsed-time threshold in ms; never a guarantee")
    run.add_argument("--output", type=Path, required=True, help="New JSON report file; never overwrite")
    run.add_argument("--summary", action="store_true", help="Print a readable summary; the saved report remains complete JSON")
    inspect = commands.add_parser("inspect", help="Validate a saved report offline without contacting the server", epilog=(
        "Exit 0 means report integrity and aggregates are valid, not that the measured campaign passed. "
        "The saved campaign status and failures remain unchanged. No network requests are made."))
    inspect.add_argument("path", type=Path, help=f"Saved report file, at most {MAX_REPORT_BYTES:,} bytes")
    inspect.add_argument("--summary", action="store_true", help="Print readable counts, latency statistics, budget observations, and limitations")
    args = parser.parse_args(argv)
    phase = "parameters"
    try:
        if args.command == "run":
            validate_endpoint(args.url)
            _parameters(args.samples, args.timeout, args.budget_ms)
            phase = "write"
            if os.path.lexists(args.output):
                raise FileExistsError
            try:
                args.output.parent.mkdir(parents=True, exist_ok=True)
            except FileExistsError as exc:
                raise NotADirectoryError from exc
            phase = "measure"
            report = asyncio.run(measure_transport(args.url, samples=args.samples, timeout_s=args.timeout, budget_ms=args.budget_ms))
            phase = "write"
            write_report(args.output, report)
            if args.summary:
                _print_summary(report, args.output)
            else:
                print(json.dumps({"report_file": str(args.output), "summary": report["summary"],
                                  "hard_real_time_qualified": False}, indent=2, allow_nan=False))
            return 0 if report["summary"]["status"] in {"observed", "observed_within_budget"} else 1
        phase = "read"
        with args.path.open("rb") as stream:
            content = stream.read(MAX_REPORT_BYTES + 1)
        if len(content) > MAX_REPORT_BYTES:
            raise ReportSizeError
        phase = "decode"
        decoded = loads_json(content.decode("utf-8"))
        phase = "validate"
        report = validate_report(decoded)
        if args.summary:
            _print_summary(report, args.path, inspected=True)
        else:
            print(json.dumps(report, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        print(f"ciw timing: {_command_error(exc, phase)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
