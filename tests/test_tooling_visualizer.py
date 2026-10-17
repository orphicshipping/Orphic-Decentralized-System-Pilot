"""An offline catalog snapshot must not gain provider or execution authority."""
import base64
from copy import deepcopy
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ciw import doctor, net, operator_catalog, operator_provision, tooling_visualizer
from ciw.control_plane import CapabilityRegistry
from ciw.workbench import Workbench


class ExportedPage(HTMLParser):
    def __init__(self, raw):
        super().__init__()
        self.parts = {"data": "", "script": "", "style": ""}
        self.active = None
        self.csp = None
        self.external_assets = []
        self.feed(raw.decode("utf-8"))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "tooling-data":
            self.active = (tag, "data")
        elif tag in {"script", "style"}:
            self.active = (tag, tag)
        if tag == "meta" and attrs.get("http-equiv", "").lower() == "content-security-policy":
            self.csp = attrs["content"]
        if tag in {"script", "img", "iframe", "link"} and ("src" in attrs or "href" in attrs):
            self.external_assets.append(attrs)

    def handle_endtag(self, tag):
        if self.active is not None and self.active[0] == tag:
            self.active = None

    def handle_data(self, data):
        if self.active is not None:
            self.parts[self.active[1]] += data

    @property
    def payload(self):
        return json.loads(base64.b64decode(self.parts["data"], validate=True))


def test_export_contains_the_entire_catalog_unchanged(monkeypatch):
    expected = operator_catalog.catalog()
    before = deepcopy(expected)
    calls = []

    def catalog():
        calls.append(True)
        return expected

    monkeypatch.setattr(operator_catalog, "catalog", catalog)
    page = ExportedPage(tooling_visualizer.render_html())
    assert calls == [True]
    assert page.payload == {"schema": "ciw.tooling-explorer.v1", "catalog": before}
    assert expected == before
    assert "net visualize" in {row["command"] for row in expected["commands"]}
    assert expected["read_only"] is True
    assert expected["authorizes_execution"] is False


def test_export_never_binds_executes_provisions_or_diagnoses(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Tooling export may only read catalog declarations")

    for name in ("bind_workflow", "bind_candidate_adapter", "execute", "execute_candidate", "replay"):
        monkeypatch.setattr(Workbench, name, forbidden)
    monkeypatch.setattr(CapabilityRegistry, "bind", forbidden)
    monkeypatch.setattr(operator_provision, "provision", forbidden)
    monkeypatch.setattr(operator_provision, "plan", forbidden)
    monkeypatch.setattr(doctor, "diagnose", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    value = ExportedPage(tooling_visualizer.render_html()).payload["catalog"]
    assert value["qualification"] == "not_performed_by_catalog"
    assert all(row["qualification"] == "not_performed_by_catalog" for row in value["workflows"])
    assert all(row["bound"] is False for row in value["builtin_providers"]["operations"].values())


def test_hostile_unicode_catalog_strings_stay_inert_and_roundtrip(monkeypatch):
    value = operator_catalog.catalog()
    hostile = '</div><script>window.PWNED=true</script><img src="https://invalid.test/pixel"> café λ ⟶'
    value["commands"][0]["purpose"] = hostile
    monkeypatch.setattr(operator_catalog, "catalog", lambda: value)
    raw = tooling_visualizer.render_html()
    page = ExportedPage(raw)
    assert hostile.encode("utf-8") not in raw
    assert b"window.PWNED" not in raw
    assert page.payload["catalog"] == value
    assert page.external_assets == []
    assert "innerHTML" not in page.parts["script"]
    assert "document.write" not in page.parts["script"]


def test_export_csp_hashes_match_inline_assets_and_network_is_disabled():
    page = ExportedPage(tooling_visualizer.render_html())
    assert page.external_assets == []
    for directive in ("default-src 'none'", "connect-src 'none'", "base-uri 'none'", "form-action 'none'"):
        assert directive in page.csp
    for kind in ("script", "style"):
        digest = base64.b64encode(sha256(page.parts[kind].encode("utf-8")).digest()).decode("ascii")
        assert f"{kind}-src 'sha256-{digest}'" in page.csp
    assert "unsafe-inline" not in page.csp


def test_write_creates_complete_snapshot_and_never_overwrites(tmp_path):
    path = tmp_path / "nested" / "tooling.html"
    tooling_visualizer.write_html(path)
    before = path.read_bytes()
    assert ExportedPage(before).payload["catalog"] == operator_catalog.catalog()
    with pytest.raises(FileExistsError):
        tooling_visualizer.write_html(path)
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_racing_destination_is_preserved_and_staging_is_cleaned(monkeypatch, tmp_path):
    path = tmp_path / "tooling.html"
    link = os.link

    def competing_writer(source, destination):
        Path(destination).write_bytes(b"concurrent output")
        link(source, destination)

    monkeypatch.setattr(tooling_visualizer.os, "link", competing_writer)
    with pytest.raises(FileExistsError):
        tooling_visualizer.write_html(path)
    assert path.read_bytes() == b"concurrent output"
    assert list(tmp_path.iterdir()) == [path]


def test_failed_export_leaves_no_partial_file_or_staging_directory(monkeypatch, tmp_path):
    def failed_link(*args, **kwargs):
        raise OSError("publication failed")

    monkeypatch.setattr(tooling_visualizer.os, "link", failed_link)
    with pytest.raises(OSError, match="publication failed"):
        tooling_visualizer.write_html(tmp_path / "tooling.html")
    assert list(tmp_path.iterdir()) == []


def test_cli_dispatch_exports_snapshot_and_reports_existing_path(tmp_path, capsys):
    path = tmp_path / "tooling.html"
    assert net.main(["visualize", "--output", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {"status": "created", "output": str(path), "read_only": True,
                      "provider_execution": False, "qualification": "not_performed_by_catalog"}
    before = path.read_bytes()
    assert net.main(["visualize", "--output", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["status"] == "refused"
    assert path.read_bytes() == before


def test_cli_module_entrypoint(tmp_path):
    path = tmp_path / "tooling.html"
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    result = subprocess.run([sys.executable, "-m", "ciw.net", "visualize", "--output", str(path)],
                            capture_output=True, text=True, env=environment)
    assert result.returncode == 0, result.stderr
    assert ExportedPage(path.read_bytes()).payload["catalog"] == operator_catalog.catalog()


@pytest.mark.parametrize("error", [OSError("unwritable output"), ValueError("bad asset"), TypeError("bad catalog")])
def test_cli_expected_errors_return_two_without_traceback(monkeypatch, capsys, error):
    def fail(path):
        raise error

    monkeypatch.setattr(tooling_visualizer, "write_html", fail)
    assert tooling_visualizer.main(["--output", "unused.html"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"status": "refused", "reason": str(error)}


def test_visualize_is_discoverable_in_top_level_and_command_help(capsys):
    for args in (["--help"], ["visualize", "--help"]):
        with pytest.raises(SystemExit) as result:
            net.main(args)
        assert result.value.code == 0
        assert "visualize" in capsys.readouterr().out
