"""Explicit single-snapshot capture. Never invoked by saved data or replay.

No discovery, configuration, authentication, subscriber access, or redirects.
The operator must bind an already-running, authorized loopback metrics endpoint.
"""
from __future__ import annotations

from datetime import datetime, timezone
import http.client
import ipaddress
import math
import time
from urllib.parse import urlsplit
import uuid

from . import connectivity as contract


def endpoint(value: str) -> tuple[str, int]:
    if (type(value) is not str or not 1 <= len(value) <= 200
            or any(ord(c) <= 32 or ord(c) > 126 for c in value) or "%" in value):
        raise ValueError("Require an explicit ASCII loopback HTTP metrics URL")
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.path != "/metrics" or "?" in value or "#" in value
            or "@" in parsed.netloc or parsed.hostname is None):
        raise ValueError("Only http://LOOPBACK:PORT/metrics without credentials, query or fragment is allowed")
    address = ipaddress.ip_address(parsed.hostname)
    if (not address.is_loopback or (address.version == 6 and str(address) != "::1")
            or parsed.port is None or not 1024 <= parsed.port <= 65535):
        raise ValueError("Bind a literal loopback IP and explicit unprivileged port")
    return str(address), parsed.port


def imported_request(context: dict, raw: bytes, *, observed_at: str, clock_id: str) -> dict:
    return contract.validate_request({"schema": contract.REQUEST_SCHEMA,
        "context": contract.validate_context(context), "source_kind": "operator_export",
        "capture": {"method": "operator_file", "observed_at": observed_at, "clock_id": clock_id,
                    "clock_uncertainty_ns": None, "elapsed_ns": None},
        "metrics": contract.project_metrics(raw, context), "transport_links": []})


def capture(context: dict, url: str, *, timeout_s: float = 3.0) -> dict:
    """One bounded GET; the URL is an operator argument, never persisted input."""
    context = contract.validate_context(context)
    host, port = endpoint(url)
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0.05 <= timeout_s <= 10:
        raise ValueError("Capture timeout must be a finite number in 0.05..10 seconds")
    start = time.monotonic_ns()
    deadline = start + int(timeout_s * 1e9)
    connection = http.client.HTTPConnection(host, port, timeout=timeout_s)
    try:
        connection.connect()
        sock = connection.sock
        connection.request("GET", "/metrics", headers={
            "Accept": "text/plain; version=0.0.4", "Accept-Encoding": "identity",
            "Connection": "close", "User-Agent": "NET-connectivity-observer/1"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"Metrics GET returned HTTP {response.status}; redirects are not followed")
        if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "text/plain":
            raise ValueError("Only plain-text Prometheus exposition is supported")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("Compressed metrics are not accepted")
        declared_length = response.getheader("Content-Length")
        if declared_length is not None and (not declared_length.isdigit() or
                not 0 < int(declared_length) <= contract.MAX_EXPORT_BYTES):
            raise ValueError("Invalid or oversized metrics Content-Length")
        transfer = response.getheader("Transfer-Encoding")
        if transfer is not None and (transfer.lower() != "chunked" or declared_length is not None):
            raise ValueError("Ambiguous or unsupported response framing")
        raw = bytearray()
        while not response.isclosed():
            remaining = (deadline - time.monotonic_ns()) / 1e9
            if remaining <= 0:
                raise TimeoutError("Metrics capture deadline exceeded")
            sock.settimeout(remaining)
            block = response.read1(min(16384, contract.MAX_EXPORT_BYTES + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
            if len(raw) > contract.MAX_EXPORT_BYTES:
                raise ValueError("Metrics response exceeds 1 MiB")
        if declared_length is not None and len(raw) != int(declared_length):
            raise ValueError("Metrics response is truncated")
        end = time.monotonic_ns()
        request = imported_request(context, bytes(raw),
            observed_at=datetime.now(timezone.utc).isoformat(), clock_id="host.capture." + uuid.uuid4().hex)
        request["capture"].update(method="loopback_http", elapsed_ns=end - start)
        return contract.validate_request(request)
    finally:
        connection.close()
