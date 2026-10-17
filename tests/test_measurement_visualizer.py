"""Evidence viewer contracts; actual-data cases use the retained portable v2 package."""
from copy import deepcopy
from hashlib import sha256
import base64
import json
import os
from pathlib import Path
import re
import shutil

import pytest

from ciw import measurement_visualizer as view


SOURCE = Path(os.environ.get("CIW_MEASUREMENT_VISUALIZER_EVIDENCE_DIR",
                            str(Path(__file__).resolve().parents[2] / "public-measurement-evidence-v2")))


@pytest.fixture
def evidence(tmp_path):
    if not SOURCE.is_dir():
        pytest.skip("Set CIW_MEASUREMENT_VISUALIZER_EVIDENCE_DIR to the retained public evidence v2 package")
    root = tmp_path / "evidence"
    for name in ("campaigns", "results"):
        for path in (SOURCE / name).rglob("*.json"):
            target = root / path.relative_to(SOURCE)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    for name in ("summary.json", "SHA256SUMS"):
        shutil.copyfile(SOURCE / name, root / name)
    return root


def rewrite(root, name, update):
    path = root / name
    data = json.loads(path.read_text(encoding="utf-8"))
    update(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    inventory = root / "SHA256SUMS"
    lines = inventory.read_text().splitlines()
    lines = [sha256(path.read_bytes()).hexdigest() + "  " + name if line[66:] == name else line for line in lines]
    inventory.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def assets(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    web = root / "web"
    web.mkdir(parents=True)
    (web / "measurement_visualizer.html").write_text(
        '<html><meta content="__CSP__"><style>__STYLE__</style>'
        '<script id="payload" type="application/octet-stream">__DATA__</script>'
        '<script>__SCRIPT__</script></html>', encoding="utf-8")
    (web / "measurement_visualizer.css").write_text("body { color: navy; }", encoding="utf-8")
    (web / "measurement_visualizer.js").write_text("'use strict';", encoding="utf-8")
    monkeypatch.setattr(view, "files", lambda _: root)
    return web


def by_id(payload):
    return {d["id"]: d for d in payload["datasets"]}


def test_actual_complete_rows_without_pdf_or_network(evidence):
    assert not list(evidence.rglob("*.pdf"))
    payload = view.build_payload(evidence)
    datasets = by_id(payload)
    assert [len(datasets[n]["rows"]) for n in ("thermal", "density", "chemistry", "dsc")] == [6, 6, 24, 180]
    assert len(datasets["thermal"]["pilot_controls"]) == 2
    assert all(not c["independent_candidate"] for c in datasets["thermal"]["pilot_controls"])
    assert payload["local_instrument_qualification"] == "not_established"
    assert payload["release_status"] == "unchanged"
    assert payload["integrity"]["status"] == "inventory_consistent"
    assert "no new scientific verification" in payload["integrity"]["scope"]
    assert payload["integrity"]["files_checked"] == 26


def test_density_retains_published_failure_and_review_rows(evidence):
    rows = by_id(view.build_payload(evidence))["density"]["rows"]
    assert sum(r["status"] == "published_rule_satisfied" for r in rows) == 3
    assert sum(r["status"] == "published_rule_not_satisfied" for r in rows) == 1
    assert sum(r["status"] == "source_review" for r in rows) == 2
    first = next(r for r in rows if r["id"] == "tridecane-azmi")
    assert first["value"] == -0.002
    assert first["uncertainty"] == pytest.approx(0.0341760149813)
    assert first["raw"]["record"]["reference_value"] == "756.990"
    assert first["source_locator"].endswith("#page=23")


def test_chemistry_does_not_promote_descriptive_intervals(evidence):
    rows = by_id(view.build_payload(evidence))["chemistry"]["rows"]
    assert {r["status"] for r in rows} == {"descriptive", "source_review"}
    row = next(r for r in rows if r["id"] == "SIM.QM-K27-L1-INM")
    assert row["group"] == "Level 1"
    assert row["value"] == -0.044
    assert row["raw"]["source"]["reported_difference"] == "-5.40"
    assert row["raw"]["source"]["reported_difference_unit"] == "%"
    assert row["raw"]["source"]["candidate_reported_coverage_factor"] == "1.96"
    assert row["raw"]["report"]["eligible_independent_comparison"] is False


def test_thermal_unknown_coverage_and_reconstruction_remain_explicit(evidence):
    dataset = by_id(view.build_payload(evidence))["thermal"]
    rows = {r["label"]: r for r in dataset["rows"]}
    assert rows["CENAM"]["value"] == -9000.0
    assert rows["CENAM"]["uncertainty"] == pytest.approx(23769.728648009426)
    assert rows["SENCAMER"]["status"] == "source_review"
    assert rows["SENCAMER"]["raw"]["report"]["reconstructed_difference_coverage_factor"] is None
    assert rows["IBMETRO"]["issue"]
    assert all(r["raw"]["report"]["reconstruction_is_reported_literal"] is False for r in dataset["rows"])
    assert "Conditional" in dataset["notes"]["criteria"]
    assert not any(r["status"].startswith("published_rule") for r in dataset["rows"])


def test_dsc_uses_observed_rate_and_no_total_uncertainty(evidence):
    dataset = by_id(view.build_payload(evidence))["dsc"]
    row = next(r for r in dataset["rows"] if r["id"] == "NIST-SP260-242-D2-rate1-rep1")
    assert (row["x"], row["value"], row["systematic_uncertainty"]) == (1.0, 28.68, 0.09)
    assert row["uncertainty"] is None
    assert (row["group"], row["sample"], row["ingot"], row["replicate"]) == ("D2", "D2", "D", 1)
    assert row["source_locator"].endswith("#page=47")
    assert "not_acquisition_order" in row["raw"]["record"]["replicate_semantics"]
    assert len({r["sample"] for r in dataset["rows"]}) == 15
    assert all(r["uncertainty"] is None for r in dataset["rows"])
    assert any(r["x"] != float(r["nominal_rate"]) for r in dataset["rows"])


def test_inventory_tamper_refused(evidence):
    path = evidence / "results/thermal/report.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        view.build_payload(evidence)


@pytest.mark.parametrize("name,update,match", [
    ("results/thermal/report.json", lambda d: d.update(manifest_sha256="sha256:" + "0"*64), "manifest hash"),
    ("results/density/report.json", lambda d: d.update(records_sha256="sha256:" + "0"*64), "Record identity"),
    ("results/chemistry/report.json", lambda d: d.update(source_sha256="sha256:" + "0"*64), "Source identity"),
    ("results/density/report.json", lambda d: d.update(local_instrument_qualification="qualified"), "qualification"),
    ("summary.json", lambda d: d.update(release_status="released"), "qualification"),
    ("results/thermal/report.json", lambda d: d["results"][0].update(id="invented"), "alignment"),
    ("results/dsc/normalized-records.json", lambda d: d["records"][0].update(enthalpy_J_per_g="999"), "original tokens"),
    ("results/chemistry/report.json", lambda d: d["results"][0].update(status="satisfies_published_rule"), "status"),
    ("results/thermal/report.json", lambda d: d["pilot_controls"][0].update(independent_candidate=True), "controls"),
    ("results/density/report.json", lambda d: d["results"][0].update(unit="mg/g"), "unit"),
    ("results/dsc/report.json", lambda d: d.update(independent_held_out_reference="established"), "holdout"),
])
def test_rehashed_internal_conflicts_refused(evidence, name, update, match):
    rewrite(evidence, name, update)
    with pytest.raises(ValueError, match=match):
        view.build_payload(evidence)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "1e400", "1e-999", True, None])
def test_bad_plot_number_refused(evidence, token):
    rewrite(evidence, "results/density/report.json", lambda d: d["results"][0].update(difference=token))
    with pytest.raises((ValueError, TypeError)):
        view.build_payload(evidence)


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_incomplete_or_repeated_rows_refused(evidence, mutation):
    def change(d):
        if mutation == "missing": d["results"].pop()
        else: d["results"][1]["id"] = d["results"][0]["id"]
    rewrite(evidence, "results/thermal/report.json", change)
    with pytest.raises(ValueError, match="table|Repeated"):
        view.build_payload(evidence)


@pytest.mark.parametrize("name", ["../outside.json", "/absolute.json", "C:/elsewhere.json", "a\\b", "a//b"])
def test_inventory_paths_are_confined(tmp_path, name):
    (tmp_path / "SHA256SUMS").write_text("0"*64 + "  " + name + "\n")
    with pytest.raises(ValueError, match="relative"):
        view.Inventory(tmp_path)


def test_duplicate_inventory_paths_refused(tmp_path):
    (tmp_path / "SHA256SUMS").write_text(("0"*64 + "  row.json\n")*2)
    with pytest.raises(ValueError, match="Duplicate"):
        view.Inventory(tmp_path)


def test_duplicate_json_key_refused(tmp_path):
    raw = b'{"a":1,"a":2}'
    (tmp_path / "row.json").write_bytes(raw)
    (tmp_path / "SHA256SUMS").write_text(sha256(raw).hexdigest() + "  row.json\n")
    with pytest.raises(ValueError, match="Duplicate JSON"):
        view.Inventory(tmp_path).read("row.json")


def test_file_budget_refused(tmp_path, monkeypatch):
    (tmp_path / "SHA256SUMS").write_text("0"*64 + "  row.json\n")
    monkeypatch.setattr(view, "FILE_LIMIT", 12)
    with pytest.raises(ValueError, match="byte budget"):
        view.Inventory(tmp_path)


def test_renderer_uses_inert_data_and_hashed_assets(assets):
    payload = {"schema": view.SCHEMA, "title": '</script><script>window.PWNED=true</script>'}
    raw = view.render_html(payload).decode()
    assert payload["title"] not in raw
    assert "connect-src 'none'" in raw
    assert "default-src 'none'" in raw
    assert "unsafe-inline" not in raw
    assert raw.count("script-src 'sha256-") == 1
    encoded = re.search(r'type="application/octet-stream">([^<]+)</script>', raw).group(1)
    assert json.loads(base64.b64decode(encoded)) == payload


def test_template_marker_missing_refused(assets):
    (assets / "measurement_visualizer.html").write_text("missing markers")
    with pytest.raises(ValueError, match="template"):
        view.render_html({"schema": view.SCHEMA})


def test_create_only_and_input_immutability(evidence, tmp_path, assets):
    before = {str(p.relative_to(evidence)): p.read_bytes() for p in evidence.rglob("*") if p.is_file()}
    output = tmp_path / "view.html"
    view.write_html(evidence, output)
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        view.write_html(evidence, output)
    assert output.read_bytes() == original
    assert before == {str(p.relative_to(evidence)): p.read_bytes() for p in evidence.rglob("*") if p.is_file()}


def test_refusal_precedes_output_and_cli_status(evidence, tmp_path, assets, capsys):
    output = tmp_path / "page.html"
    assert view.main(["--evidence-dir", str(evidence), "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["provider_execution"] is False
    assert view.main(["--evidence-dir", str(evidence), "--output", str(output)]) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    rewrite(evidence, "summary.json", lambda d: d.update(local_instrument_qualification="qualified"))
    output2 = tmp_path / "not-created" / "page.html"
    assert view.main(["--evidence-dir", str(evidence), "--output", str(output2)]) == 1
    assert not output2.parent.exists()


def test_actual_packaged_assets_render(evidence):
    payload = view.build_payload(evidence)
    raw = view.render_html(payload).decode("utf-8")
    assert all(marker not in raw for marker in ("__CSP__", "__STYLE__", "__SCRIPT__", "__DATA__"))
    assert "connect-src 'none'" in raw
    assert "unsafe-inline" not in raw
    assert len(raw) < 12 * 1024 * 1024
    encoded = base64.b64encode(json.dumps(payload, allow_nan=False, ensure_ascii=True).encode()).decode()
    assert encoded in raw
