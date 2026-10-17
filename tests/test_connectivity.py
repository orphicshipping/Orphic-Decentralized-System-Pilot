"""Public synthetic tests; no private-network/subscriber evidence or RF activity."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
from threading import Thread
from time import sleep
from types import SimpleNamespace

import pytest

from ciw import connectivity as c
from ciw import connectivity_capture as capture
from ciw import connectivity_workflow as w
from ciw.control_contracts import bytes_ref, save_new
from ciw.core.identities import canonical_json, evidence_id
from ciw.operations.registry import Operation, OperationRegistry
from ciw.operations.runner import seal
from ciw.session import Session, loads_json


@pytest.mark.parametrize("provider", ["open5gs", "free5gc"])
def test_both_profiles(provider):
    request = c.example_request(provider)
    value = c.observe(request)
    assert value["status"] == "OBSERVED"
    assert value["source_kind"] == "synthetic"
    assert value["authority"] == c.AUTHORITY
    assert value["transport_links"][0]["sample_to_receive_delta_ns"] is None
    assert value["transport_links"][0]["status"] == "CLOCK_RELATION_UNKNOWN"
    assert value["whole_export_integrity"] == "unverifiable_without_original_export_bytes"
    assert c.validate_request(request) == request
    assert c.validate_request(request) is not request


@pytest.mark.parametrize("provider", ["open5gs", "free5gc"])
def test_projection_preserves_exact_selected_bytes_and_discards_others(provider):
    context = c.example_context(provider)
    raw = b'# HELP unrelated private text\nsecret_metric{supi="not-a-real-subscriber"} 9\n' + c.example_metrics(provider).replace(b"\n", b"\r\n")
    projected = c.project_metrics(raw, context)
    assert projected["input_ref"] == bytes_ref(raw)
    assert projected["input_bytes"] == len(raw)
    original_lines = raw.decode().splitlines(keepends=True)
    for row in projected["selected_lines"]:
        assert row["text"] == original_lines[row["line"] - 1]
    assert "private" not in canonical_json(projected)
    assert "subscriber" not in canonical_json(projected)
    assert "supi" not in canonical_json(projected)
    assert projected["whole_export_retained"] is False


@pytest.mark.parametrize("bad", [
    b"# TYPE gnb counter\ngnb 1\n", b"gnb 1\n",
    b"# TYPE gnb gauge\ngnb NaN\n", b"# TYPE gnb gauge\ngnb +Inf\n",
    b"# TYPE gnb gauge\ngnb -1\n", b"# TYPE gnb gauge\ngnb 0.5\n",
    b"# TYPE gnb gauge\ngnb 1e-400\n", b"# TYPE gnb gauge\ngnb 1e400\n",
    b"# TYPE gnb gauge\ngnb 9007199254740992\n",
    b"# TYPE gnb gauge\ngnb 1 123456\n",
    b'# TYPE gnb gauge\ngnb{supi="secret"} 1\n',
    b"# TYPE gnb gauge\ngnb 1\ngnb 1\n",
    b"# TYPE gnb gauge\n# TYPE gnb gauge\ngnb 1\n",
    b"# TYPE gnb gauge\ngnb 1 # {trace_id=\"x\"} 1\n",
    b"# TYPE gnb gauge\ngnb 1\x00\n", b"\xff", b"",
    pytest.param(b"x" * (c.MAX_EXPORT_BYTES + 1), id="over-export-byte-limit"),
])
def test_bad_metrics_refuse(bad):
    with pytest.raises((ValueError, UnicodeError)):
        c.project_metrics(bad, c.example_context())


@pytest.mark.parametrize("token,expected", [("0", 0), ("2e0", 2), ("1.000", 1), ("+3", 3), ("9007199254740991", 2**53 - 1)])
def test_exact_counts(token, expected):
    data = c.project_metrics(f"# TYPE gnb gauge\ngnb {token}\n".encode(), c.example_context())
    assert c.validate_projection(data, c.example_context())[0]["value"] == expected


@pytest.mark.parametrize("labels", ['access_type="3GPP_ACCESS",supi="x"', 'access_type="x"',
                                  'access_type="3GPP_ACCESS",access_type="3GPP_ACCESS"', '', 'other="x"'])
def test_free5gc_labels_strict(labels):
    context = c.example_context("free5gc")
    name = c.metric_names(context)[0]
    with pytest.raises(ValueError):
        c.project_metrics(f"# TYPE {name} gauge\n{name}{{{labels}}} 2\n".encode(), context)


@pytest.mark.parametrize("namespace", ["", "lab", "NET_test_2"])
def test_namespace_is_explicit(namespace):
    context = c.example_context("free5gc")
    context["metrics_namespace"] = namespace
    name = c.metric_names(context)[0]
    raw = f'# TYPE {name} gauge\n{name}{{access_type="3GPP_ACCESS"}} 0\n'.encode()
    assert c.validate_projection(c.project_metrics(raw, context), context)[0]["value"] == 0


def test_missing_metrics_stay_missing():
    request = c.example_request()
    request["metrics"] = c.project_metrics(b"# no supported aggregates\n", request["context"])
    result = c.observe(request)
    assert result["status"] == "MISSING_METRICS"
    assert result["metrics"] == []
    assert len(result["missing_metrics"]) == 3


@pytest.mark.parametrize("field,value", [
    ("provider", "arbitrary.module"), ("component", "mme"), ("core_mode", "lte"),
    ("provider_revision", "main"), ("metrics_namespace", "other"),
    ("instance_id", "imsi-not-real"), ("network_id", "http://example.com"),
    ("data_network", "a\nb"), ("configuration_ref", "0" * 64),
])
def test_context_refusals(field, value):
    context = c.example_context()
    context[field] = value
    with pytest.raises(ValueError):
        c.validate_context(context)


@pytest.mark.parametrize("mcc,mnc", [(1, "01"), ("01", "01"), ("001", 1), ("001", "1"), ("001", "0001"), ("００１", "01")])
def test_plmn_text_width(mcc, mnc):
    context = c.example_context()
    context["plmn"] = {"mcc": mcc, "mnc": mnc}
    with pytest.raises(ValueError):
        c.validate_context(context)


def test_plmn_and_slice_are_not_normalized():
    context = c.example_context()
    context["plmn"]["mnc"] = "001"
    context["slice"]["sd"] = "00AaF0"
    assert c.validate_context(context) == context
    context["slice"]["sst"] = True
    with pytest.raises(ValueError):
        c.validate_context(context)


def test_unknown_authority_and_secret_fields_refused():
    request = c.example_request()
    request["context"]["subscriber_key"] = "never-store"
    with pytest.raises(ValueError):
        c.validate_request(request)
    request = c.example_request()
    request["authority"] = {"radio_transmission": True}
    with pytest.raises(ValueError):
        c.validate_request(request)


@pytest.mark.parametrize("field,value", [("sample_time_ns", True), ("receive_time_ns", 2**63), ("sample_time_ns", 1.0), ("receive_time_ns", -1)])
def test_timestamp_types(field, value):
    request = c.example_request()
    request["transport_links"][0][field] = value
    with pytest.raises(ValueError):
        c.validate_request(request)


@pytest.mark.parametrize("delta", [0, 30000000, -1])
def test_same_clock_exact_timestamp_difference(delta):
    request = c.example_request()
    link = request["transport_links"][0]
    link["receive_clock_id"] = link["sample_clock_id"]
    link["sample_time_ns"] = 2**62
    link["receive_time_ns"] = 2**62 + delta
    result = c.observe(request)["transport_links"][0]
    assert result["sample_to_receive_delta_ns"] == delta
    assert result["uncertainty_ns"] is None
    assert result["status"] == ("INCONSISTENT_DECLARED_ORDER" if delta < 0 else "SAME_DECLARED_CLOCK")
    assert result["observation_ref"] == link["observation_ref"]


def test_capture_clock_and_source_kind():
    for replacement in ({"observed_at": "2026-10-05T00:00:00"}, {"clock_uncertainty_ns": True},
                        {"elapsed_ns": -1}, {"method": "loopback_http"}):
        request = c.example_request()
        request["capture"].update(replacement)
        with pytest.raises(ValueError):
            c.validate_request(request)


@pytest.mark.parametrize("mutation", ["line", "text", "size", "whole", "schema"])
def test_projection_metadata_mutations(mutation):
    request = c.example_request()
    projection = request["metrics"]
    if mutation == "line":
        projection["selected_lines"][1]["line"] = projection["selected_lines"][0]["line"]
    elif mutation == "text":
        projection["selected_lines"][0]["text"] = "# HELP gnb credentials\n"
    elif mutation == "size":
        projection["input_bytes"] = 1
    elif mutation == "whole":
        projection["whole_export_retained"] = True
    else:
        projection["schema"] = "different"
    with pytest.raises(ValueError):
        c.validate_request(request)


@pytest.mark.parametrize("url", [
    "http://example.com:9090/metrics", "http://192.168.1.1:9090/metrics",
    "http://169.254.169.254:8080/metrics", "http://127.0.0.1/metrics",
    "https://127.0.0.1:9090/metrics", "http://127.0.0.1:80/metrics",
    "http://127.0.0.1:9090/subscribers", "http://user:pass@127.0.0.1:9090/metrics",
    "http://127.0.0.1:9090/metrics?target=other", "http://127.0.0.1:9090/metrics#fragment",
    "http://localhost:9090/metrics", "http://127.1:9090/metrics",
    "http://2130706433:9090/metrics", "http://[::ffff:127.0.0.1]:9090/metrics",
    "http://[::1%lo]:9090/metrics", " http://127.0.0.1:9090/metrics",
    "http://127.0.0.1:9090/metrics\n", "file:///metrics",
])
def test_endpoint_refusals_without_network(url, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network used before admission"))
    with pytest.raises(ValueError):
        capture.capture(c.example_context(), url)


@pytest.mark.parametrize("url,expected", [("http://127.0.0.5:9090/metrics", ("127.0.0.5", 9090)),
                                          ("http://[::1]:19090/metrics", ("::1", 19090))])
def test_explicit_loopback_endpoints(url, expected):
    assert capture.endpoint(url) == expected


@contextmanager
def local_exporter(raw, *, status=200, mime="text/plain; version=0.0.4", encoding=None,
                   declared_length=None, framing="length", trickle_headers=False):
    if framing not in ("length", "close", "chunked", "ambiguous"):
        raise ValueError("Invalid synthetic exporter framing")
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(status)
            if trickle_headers:
                try:
                    self.flush_headers()
                    for _ in range(10):
                        self.send_header("X-Synthetic-Progress", "fixture")
                        self.flush_headers()
                        sleep(0.02)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                calls.append("headers-complete")
            self.send_header("Content-Type", mime)
            if encoding:
                self.send_header("Content-Encoding", encoding)
            if framing in ("length", "ambiguous"):
                self.send_header("Content-Length", str(len(raw) if declared_length is None else declared_length))
            if framing in ("chunked", "ambiguous"):
                self.send_header("Transfer-Encoding", "chunked")
            if status == 302:
                self.send_header("Location", "http://example.invalid/never-follow")
            self.end_headers()
            try:
                if framing in ("chunked", "ambiguous"):
                    for chunk in (raw[:10], raw[10:]):
                        if chunk:
                            self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/metrics", calls
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.parametrize("provider", ["open5gs", "free5gc"])
@pytest.mark.parametrize("same_tick", [False, True], ids=["host-clock", "same-clock-tick"])
def test_actual_http_capture_and_native_session(provider, tmp_path, monkeypatch, same_tick):
    # This is a real local HTTP transport test with a synthetic exporter, NOT
    # execution of Open5GS/free5GC or authentication of an operator declaration.
    if same_tick:
        monkeypatch.setattr(capture, "time", SimpleNamespace(monotonic_ns=lambda: 1000000000))
    with local_exporter(c.example_metrics(provider)) as (url, calls):
        request = capture.capture(c.example_context(provider), url)
        assert calls == ["/metrics"]
    assert request["capture"]["method"] == "loopback_http"
    # Clock resolution can place the complete GET within one monotonic tick.
    # Preserve the observed zero; do not invent positive timing precision.
    assert type(request["capture"]["elapsed_ns"]) is int
    assert request["capture"]["elapsed_ns"] >= 0
    if same_tick:
        assert request["capture"]["elapsed_ns"] == 0
    assert request["capture"]["clock_uncertainty_ns"] is None
    assert url not in canonical_json(request)
    result = w.run(request, tmp_path / "run")
    assert result["status"] == "OBSERVED"
    assert result["observation"]["authority"]["core_execution"] == "not_performed"


@pytest.mark.parametrize("stage", ["headers", "interim", "chunk-framing"])
def test_total_deadline_bounds_each_response_read(stage, monkeypatch):
    # Model socket idle timeouts deterministically while using the real
    # HTTPConnection/HTTPResponse parser. No external endpoint is contacted.
    payload = c.example_metrics()
    headers = (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
               + str(len(payload)).encode() + b"\r\nConnection: close\r\n\r\n")
    if stage == "headers":
        chunks = [b"HTTP/1.1 200 OK\r\n", b"Content-Type: text/plain\r\n",
                  b"Content-Length: " + str(len(payload)).encode() + b"\r\n",
                  b"Connection: close\r\n\r\n", payload]
    elif stage == "interim":
        chunks = [b"HTTP/1.1 100 Continue\r\n\r\n"] * 4 + [headers, payload]
    else:
        chunks = [b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n",
                  b"1", b"\r", b"\n", b"x\r\n", b"0\r\n\r\n"]
    clock = SimpleNamespace(ns=0)
    state = SimpleNamespace(timeout=0.05, closed=False)

    class Trickle(io.RawIOBase):
        def readable(self):
            return True

        def readinto(self, target):
            delay_ns = 20000000
            allowance_ns = round(state.timeout * 1000000000)
            clock.ns += min(delay_ns, allowance_ns)
            if allowance_ns < delay_ns:
                raise TimeoutError("Synthetic socket idle timeout")
            if not chunks:
                return 0
            data = chunks[0][:len(target)]
            remainder = chunks[0][len(target):]
            if remainder:
                chunks[0] = remainder
            else:
                chunks.pop(0)
            target[:len(data)] = data
            return len(data)

    stream = Trickle()

    class SelectedSocket:
        def makefile(self, mode):
            assert mode == "rb"
            return io.BufferedReader(stream)

        def settimeout(self, value):
            state.timeout = value

        def sendall(self, data):
            assert data.startswith(b"GET /metrics ")

        def close(self):
            state.closed = True

    socket_fixture = SelectedSocket()
    original_connection = capture.http.client.HTTPConnection

    def connection(host, port, *, timeout):
        assert (host, port) == ("127.0.0.1", 9090)
        state.timeout = timeout
        value = original_connection(host, port, timeout=timeout)
        value.sock = socket_fixture
        value.connect = lambda: None
        return value

    monkeypatch.setattr(capture.http.client, "HTTPConnection", connection)
    monkeypatch.setattr(capture, "time", SimpleNamespace(monotonic_ns=lambda: clock.ns))
    with pytest.raises(TimeoutError):
        capture.capture(c.example_context(), "http://127.0.0.1:9090/metrics", timeout_s=0.05)
    assert clock.ns <= 50000000, "Per-read idle timeouts must not extend the total capture deadline"
    assert state.closed and stream.closed


def test_real_http_deadline_stops_trickled_headers():
    with local_exporter(c.example_metrics(), trickle_headers=True) as (url, calls):
        with pytest.raises(TimeoutError):
            capture.capture(c.example_context(), url, timeout_s=0.05)
        assert calls == ["/metrics"], "Capture waited for the complete slow header stream"


@pytest.mark.parametrize("options", [{"status": 302}, {"status": 403}, {"mime": "text/html"},
    {"encoding": "gzip"}, {"declared_length": c.MAX_EXPORT_BYTES + 1}, {"declared_length": 100000}])
def test_http_errors_do_not_follow_or_return_success(options):
    with local_exporter(c.example_metrics(), **options) as (url, calls):
        with pytest.raises((ValueError, OSError)):
            capture.capture(c.example_context(), url)
        assert calls == ["/metrics"]


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), 0, 11])
def test_bad_timeouts(value):
    with pytest.raises(ValueError):
        capture.capture(c.example_context(), "http://127.0.0.1:9090/metrics", timeout_s=value)


@pytest.mark.parametrize("provider", ["open5gs", "free5gc"])
def test_native_lifecycle_and_read_only_inspection(provider, tmp_path, monkeypatch):
    request = c.example_request(provider)
    raw = (json.dumps(request, indent=3) + "\n\n").encode()
    initial = w.run(request, tmp_path / "first", raw=raw)
    before = {p.name: p.read_bytes() for p in (tmp_path / "first").iterdir() if p.is_file()}
    monkeypatch.setattr(capture, "capture", lambda *a, **k: pytest.fail("offline workflow attempted capture"))
    original_execute = w._execute
    monkeypatch.setattr(w, "_execute", lambda *a, **k: pytest.fail("inspection invoked bound provider"))
    assert w.inspect(tmp_path / "first") == initial
    report = w.report(tmp_path / "first", tmp_path / "report.html")
    assert report["network_access"] is False
    page = (tmp_path / "report.html").read_text()
    assert "synthetic" in page and "NOT VERIFIED" in page and "<script" not in page
    monkeypatch.setattr(w, "_execute", original_execute)
    replay = w.replay(tmp_path / "first", tmp_path / "second")
    assert initial["evidence_id"] == replay["evidence_id"]
    assert initial["execution_id"] != replay["execution_id"]
    assert initial["result_id"] != replay["result_id"]
    assert initial["operation_id"] == replay["operation_id"] == c.OPERATION
    assert initial["verification_status"] == "not_verified"
    assert (tmp_path / "second/request.json").read_bytes() == raw
    assert before == {p.name: p.read_bytes() for p in (tmp_path / "first").iterdir() if p.is_file()}
    with pytest.raises(FileExistsError):
        w.run(request, tmp_path / "first")
    with pytest.raises(FileExistsError):
        w.report(tmp_path / "first", tmp_path / "report.html")


@pytest.mark.parametrize("mode", ["raw", "result", "authority", "runtime", "selection", "source"])
def test_retained_tampering_refuses(mode, tmp_path):
    w.run(c.example_request(), tmp_path / "run")
    path = tmp_path / "run/workspace.json"
    workspace = loads_json(path.read_text())
    if mode == "raw":
        with (tmp_path / "run/request.json").open("a") as stream:
            stream.write(" ")
    elif mode in ("result", "authority"):
        result = workspace["results"][0]
        if mode == "result":
            result["data"]["metrics"][0]["value"] = 99
        else:
            result["data"]["authority"]["radio_transmission"] = "authorized"
        seal(result)
    elif mode == "runtime":
        result = workspace["results"][0]
        result["runtime"]["code_sha256"] = "0" * 64
        seal(result)
    elif mode == "selection":
        workspace["results"][0]["interval_s"] = [0.0, 0.5]
        workspace["executions"][0]["interval_s"] = [0.0, 0.5]
        seal(workspace["results"][0])
        seal(workspace["executions"][0])
    else:
        workspace["run"]["metadata"]["connectivity_request"]["context"]["network_id"] = "changed"
        workspace["run"]["evidence_id"] = evidence_id(workspace["run"])
    if mode != "raw":
        path.write_text(json.dumps(workspace))
    with pytest.raises(ValueError):
        w.inspect(tmp_path / "run")


def test_native_refusal_and_unbound_reopen(tmp_path):
    request = c.example_request()
    source = w.make_source(request, bytes_ref(b"source-declaration"))
    w.register_schema()
    unbound = Session(source, tmp_path / "unbound", operations=OperationRegistry())
    response = unbound.handle({"protocol_version": 1, "request_id": "missing-provider", "type": "operation.execute",
                              "payload": {"operation_id": c.OPERATION, "parameters": {}}})
    assert len(unbound.executions) == 1 and not unbound.results
    assert next(iter(unbound.executions.values()))["status"] == "refused"
    unbound.save_workspace(tmp_path / "workspace.json")
    reopened = Session.from_workspace(tmp_path / "workspace.json", tmp_path / "reopened")
    assert reopened.executions == unbound.executions
    assert reopened.results == {}


def test_native_bad_parameters_refused_before_any_network(tmp_path):
    source = w.make_source(c.example_request(), bytes_ref(b"source-declaration"))
    session = Session(source, tmp_path / "session", operations=w.registry())
    session.handle({"protocol_version": 1, "request_id": "bad-parameters", "type": "operation.execute",
        "payload": {"operation_id": c.OPERATION, "parameters": {"endpoint": "http://example.invalid"}}})
    assert next(iter(session.executions.values()))["status"] == "refused"
    assert not session.results


def test_cli_existing_destination_prevents_get(tmp_path, monkeypatch, capsys):
    from ciw.connectivity_cli import main
    save_new(tmp_path / "context.json", c.example_context())
    (tmp_path / "existing").write_text("keep")
    monkeypatch.setattr(capture, "capture", lambda *a, **k: pytest.fail("GET issued despite existing destination"))
    assert main(["capture", str(tmp_path / "context.json"), "--endpoint", "http://127.0.0.1:9090/metrics",
                 "--output", str(tmp_path / "existing")]) == 1
    assert (tmp_path / "existing").read_text() == "keep"
    assert "REFUSED" in capsys.readouterr().err


def test_strict_json_and_regular_files(tmp_path):
    raw = json.dumps(c.example_request()).encode()
    with pytest.raises(ValueError):
        w.parse_request(raw.replace(b'"schema":', b'"schema":"duplicate","schema":', 1))
    with pytest.raises(ValueError):
        w.parse_request(b"".join([b" "] * (c.MAX_REQUEST_BYTES + 1)))
    target = tmp_path / "request.json"
    target.write_bytes(raw)
    linked = tmp_path / "link.json"
    try:
        linked.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"OS does not permit symlink creation: {exc}")
    with pytest.raises(ValueError):
        w.read_bytes(linked, limit=c.MAX_REQUEST_BYTES)


def test_top_level_net_command_and_review_catalog():
    from ciw.net import main
    from ciw.operator_commands import COMMANDS
    from ciw.scientific_audit import audit
    assert any(row[0] == "connectivity" for row in COMMANDS)
    assert main(["connectivity", "catalog"]) == 0
    review = audit()
    assert not [item for item in review["issues"] if item.get("name") == "connectivity"]


def test_current_view_selection_does_not_rewrite_historical_operation(tmp_path):
    initial = w.run(c.example_request(), tmp_path / "run")
    path = tmp_path / "run/workspace.json"
    workspace = loads_json(path.read_text())
    workspace["selection"]["interval_s"] = [0.0, 0.5]
    path.write_text(json.dumps(workspace))
    assert w.inspect(tmp_path / "run") == initial


@pytest.mark.parametrize("framing", ["length", "close", "chunked"])
def test_actual_http_framing_without_proxy(framing, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://192.0.2.1:1234")
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:1234")
    monkeypatch.setenv("NO_PROXY", "")
    with local_exporter(c.example_metrics(), framing=framing) as (url, calls):
        request = capture.capture(c.example_context(), url)
    assert c.observe(request)["status"] == "OBSERVED"
    assert calls == ["/metrics"]


def test_ambiguous_http_framing_refuses():
    with local_exporter(c.example_metrics(), framing="ambiguous") as (url, calls):
        with pytest.raises(ValueError, match="framing"):
            capture.capture(c.example_context(), url)
    assert calls == ["/metrics"]


def test_unframed_body_budget_refuses():
    with local_exporter(b"#" * (c.MAX_EXPORT_BYTES + 1), framing="close") as (url, _calls):
        with pytest.raises(ValueError, match="1 MiB"):
            capture.capture(c.example_context(), url)


@pytest.mark.parametrize("ending", ["\n\n", "\r\n\n", "\r", "\v"])
def test_projection_selected_line_cannot_claim_multiple_lines(ending):
    request = c.example_request()
    request["metrics"]["selected_lines"][0]["text"] = "# TYPE ran_ue gauge" + ending
    with pytest.raises(ValueError):
        c.validate_request(request)


@pytest.mark.parametrize("provider", ["open5gs", "free5gc"])
@pytest.mark.parametrize("mode", ["import", "capture"])
def test_positive_cli_ingress(provider, mode, tmp_path, capsys):
    from ciw.net import main
    context = tmp_path / "context.json"
    assert main(["connectivity", "context", "--provider", provider, "--output", str(context)]) == 0
    output = tmp_path / "request.json"
    if mode == "import":
        raw = tmp_path / "metrics.prom"
        raw.write_bytes(c.example_metrics(provider))
        args = [mode, str(context), str(raw), "--observed-at", "2026-10-05T00:00:00Z",
                "--clock-id", "synthetic.collector.1", "--output", str(output)]
        assert main(["connectivity", *args]) == 0
    else:
        with local_exporter(c.example_metrics(provider)) as (url, calls):
            assert main(["connectivity", mode, str(context), "--endpoint", url, "--output", str(output)]) == 0
            assert calls == ["/metrics"]
    assert main(["connectivity", "run", str(output), "--output-dir", str(tmp_path / "run")]) == 0
    assert w.inspect(tmp_path / "run")["status"] == "OBSERVED"
    assert "whole_export_retained" in capsys.readouterr().out


def test_cli_missing_aggregates_has_nonzero_status(tmp_path, capsys):
    from ciw.net import main
    request = c.example_request()
    request["metrics"] = c.project_metrics(b"# none selected\n", request["context"])
    save_new(tmp_path / "request.json", request)
    args = ["connectivity", "run", str(tmp_path / "request.json"), "--output-dir", str(tmp_path / "run")]
    assert main(args) == 2
    assert main(["connectivity", "inspect", str(tmp_path / "run")]) == 2
    assert "MISSING_METRICS" in capsys.readouterr().out
