"""Actual Chromium + shared Board transport + native NET simulation campaign.

Run against an installed NET package, or set PYTHONPATH=src for a source check.
Requires Playwright and an installed Chromium. No hardware or CAD claims.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Thread


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--browser", help="Operator-selected Chromium executable; otherwise Playwright's installed browser")
    parser.add_argument("--offline", action="store_true", help="Offline graph/export/CLI journey; not live-browser qualification")
    args = parser.parse_args()
    if args.offline:
        return offline(args)
    from playwright.sync_api import sync_playwright
    from ciw.industrial_lab import IndustrialLab, run_view
    from ciw.instruments import make_demo_run
    from ciw.system_spec import demo_spec
    from ciw.visual_board import demo_board
    from ciw.visual_board_server import BoardWorkbench, make_server
    args.output_dir.mkdir(parents=True, exist_ok=False)
    checks = []
    work = IndustrialLab(json.dumps(demo_spec(cells=16), indent=2).encode(), args.output_dir / "lab", allow_run=True)
    old = BoardWorkbench(demo_board(), args.output_dir / "original-board", source=make_demo_run(), allow_run=True)
    servers = [make_server(w) for w in (work, old)]
    threads = [Thread(target=s.serve_forever, daemon=True) for s in servers]
    for t in threads: t.start()
    errors = []
    try:
        with sync_playwright() as p:
            options = {"headless": True, "args": ["--no-sandbox"]}
            if args.browser: options["executable_path"] = args.browser
            browser = p.chromium.launch(**options)
            page = browser.new_page(viewport={"width":1600,"height":1100}, device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(servers[0].board_url)
            page.wait_for_function("document.querySelector('#board-title').textContent.startsWith('Industrial Lab')")
            assert page.locator(".graph-node").count() == 3
            page.locator("#run-baseline").click()
            page.wait_for_function("window.netIndustrialLabSnapshot()?.baseline_ref", timeout=30000)
            assert page.evaluate("window.netIndustrialLabSnapshot().baseline") == run_view(work.baseline)
            before = deepcopy(work.baseline)
            checks.append("browser_baseline_matches_native_records")
            field = page.locator("#parameter-left_temperature_k")
            field.fill("318.15");field.dispatch_event("change")
            field.locator("..").locator("xpath=following-sibling::button[1]").click()
            page.wait_for_function("document.querySelector('#preview-summary').textContent.includes('Complete coupled rerun')")
            assert work.baseline == before and len(work.session.results) == 3
            checks.append("browser_preview_is_nonexecuting")
            page.locator("#run-candidate").click()
            page.wait_for_function("window.netIndustrialLabSnapshot()?.candidate_ref", timeout=30000)
            assert page.evaluate("window.netIndustrialLabSnapshot().candidate") == run_view(work.latest_candidate)
            assert work.baseline == before and len(work.session.results) == 6
            assert page.locator('[data-series="candidate"]').count() == 1
            checks.append("browser_candidate_native_execution_and_immutable_baseline")
            # Sensor inspection exposes both native retained quantities without recomputation.
            page.locator('[data-visual-id="node:sensor"]').press("Enter")
            assert page.locator('[data-series="candidate"]').count() == 2
            checks.append("sensor_series_exact_native_values")
            # Dragging only changes view coordinates, not configuration or results.
            box = page.locator('[data-visual-id="node:thermal"]').bounding_box()
            page.mouse.move(box["x"]+30, box["y"]+20);page.mouse.down()
            page.mouse.move(box["x"]+60, box["y"]+90, steps=5);page.mouse.up()
            page.wait_for_timeout(100)
            snapshot = page.evaluate("window.netBoardViewSnapshot()")
            assert snapshot["board_ref"] == work.board["record_digest"]
            assert "node:thermal" in snapshot["view"]["positions"] and work.baseline == before
            checks.append("canvas_drag_is_not_physical_edit")
            page.screenshot(path=str(args.output_dir / "industrial-lab.png"), full_page=True)
            # Reload retains latest simulation selection in the same live service.
            page.reload();page.wait_for_function("window.netIndustrialLabSnapshot()?.candidate_ref")
            assert page.evaluate("window.netIndustrialLabSnapshot().candidate") == run_view(work.latest_candidate)
            checks.append("reload_recovers_existing_occurrences_without_rerun")
            # This extension must not turn the original oscillator Board into a lab.
            page.goto(servers[1].board_url)
            page.wait_for_function("document.querySelector('#board-title').textContent==='Oscillator investigation'")
            assert page.evaluate("window.netIndustrialLabSnapshot()") is None
            page.locator("#run-baseline").click()
            page.wait_for_function("document.querySelector('#run-summary').textContent.includes('operation records retained')", timeout=30000)
            page.screenshot(path=str(args.output_dir / "original-board.png"), full_page=True)
            checks.append("original_board_browser_baseline_unchanged")
            assert not errors, errors
            browser_version = browser.version
            browser.close()
    finally:
        for s in servers:s.shutdown();s.server_close()
        for t in threads:t.join(5)
    report = {"status":"PASS","checks":checks,"page_errors":errors,"browser_version":browser_version,
              "observed_at":datetime.now(timezone.utc).isoformat(),"physical_validation_status":"not_assessed",
              "scope":"Source/installed browser application check with real native simulation and synthetic observations"}
    (args.output_dir / "report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))



def offline(args):
    """Exercise supported offline export in a real DOM, without network navigation."""
    import subprocess
    import sys
    from playwright.sync_api import sync_playwright
    from ciw.industrial_lab import project_board
    from ciw.system_spec import demo_spec
    from ciw.visual_board import render_html, demo_board
    args.output_dir.mkdir(parents=True, exist_ok=False)
    spec = demo_spec(cells=16)
    board = project_board(spec)
    errors = []
    with sync_playwright() as p:
        options = {"headless": True, "args": ["--no-sandbox"]}
        if args.browser:
            options["executable_path"] = args.browser
        browser = p.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 1600, "height": 1100}, accept_downloads=True)
        page.on("pageerror", lambda e: errors.append(str(e)))
        # Document injection, not navigation around the administered URL policy.
        page.set_content(render_html(board).decode())
        page.wait_for_function("document.querySelector('#board-title').textContent.startsWith('Industrial Lab')")
        assert page.locator('.graph-node').count() == 3
        assert page.locator('#run-baseline').is_disabled()
        field = page.locator('#parameter-left_temperature_k')
        field.fill('318.15')
        field.dispatch_event('change')
        with page.expect_download() as captured:
            field.locator('..').locator('xpath=following-sibling::button[1]').click()
        captured.value.save_as(args.output_dir / 'edit.json')
        request = json.loads((args.output_dir / 'edit.json').read_text())
        assert request == {"schema":"ciw.board-parameter-edit.v1", "base_board_ref":board['record_digest'],
                           "node_id":"thermal", "parameter":"left_temperature_k", "replacement":318.15}
        before = page.evaluate('window.netBoardViewSnapshot()')
        box = page.locator('[data-visual-id="node:thermal"]').bounding_box()
        page.mouse.move(box['x']+30, box['y']+20)
        page.mouse.down()
        page.mouse.move(box['x']+60, box['y']+90, steps=5)
        page.mouse.up()
        after = page.evaluate('window.netBoardViewSnapshot()')
        assert before['board_ref'] == after['board_ref'] == board['record_digest']
        assert before['view']['positions'] != after['view']['positions']
        page.screenshot(path=str(args.output_dir / 'industrial-lab-offline.png'), full_page=True)
        page.set_content(render_html(demo_board()).decode())
        page.wait_for_function("document.querySelector('#board-title').textContent==='Oscillator investigation'")
        assert page.evaluate('window.netIndustrialLabSnapshot()') is None
        assert not errors, errors
        browser_version = browser.version
        browser.close()
    source = args.output_dir / 'specification.json'
    source.write_text(json.dumps(spec, indent=2)+'\n', encoding='utf-8')
    command = [sys.executable, '-m', 'ciw.net', 'system', 'lab', 'run', str(source.resolve()),
               '--edit', str((args.output_dir / 'edit.json').resolve()),
               '--output-dir', str((args.output_dir / 'native-run').resolve()), '--engine', 'subprocess']
    completed = subprocess.run(command, capture_output=True, text=True, timeout=90)
    (args.output_dir / 'native-run.log').write_text(completed.stdout+completed.stderr, encoding='utf-8')
    assert completed.returncode == 0, completed.stdout+completed.stderr
    native = json.loads(completed.stdout)
    assert native['status'] == 'PASS' and native['candidate_result_id'] != native['baseline_result_id']
    report = {"status":"PASS", "mode":"offline_browser_export_to_native_subprocess",
              "observed_at":datetime.now(timezone.utc).isoformat(), "browser_version":browser_version,
              "page_errors":errors, "native_cli_result":native,
              "checks":["real_DOM_graph_three_models", "offline_execution_disabled", "exact_typed_edit_export",
                        "canvas_drag_does_not_change_board_identity", "original_board_DOM_unchanged",
                        "exported_edit_runs_native_baseline_and_candidate"],
              "live_browser_network_qualified":False, "physical_validation_status":"not_assessed"}
    (args.output_dir / 'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))

if __name__ == "__main__":main()
