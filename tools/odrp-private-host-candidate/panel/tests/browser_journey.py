"""Real Chromium -> authenticated BFF -> gateway/Worker -> installed MCP.

The Worker runs with the repository's SQLite/file DO/R2 substitutes. Access
signatures are real RS256 over synthetic identities; no hosted service, TLS
deployment, physical model qualification, or comprehensive WCAG claim is made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import time
import traceback

from playwright.sync_api import sync_playwright


class Fixture:
    def __init__(self, args):
        self.args, self.process, self.log = args, None, None
        self.sequence = 0
        self.info = None
        self.cleanup = []

    def receive(self, deadline=120):
        until = time.monotonic() + deadline
        while time.monotonic() < until:
            if self.process.poll() is not None:
                raise RuntimeError("vertical fixture exited; inspect fixture-stderr.log")
            ready, _, _ = select.select([self.process.stdout], [], [], min(1, until - time.monotonic()))
            if ready:
                line = self.process.stdout.readline()
                if line:
                    return json.loads(line)
        raise TimeoutError("vertical fixture control deadline exceeded")

    def start(self, *, resume=False):
        script = Path(__file__).with_name("vertical_server.mjs")
        self.log = (self.args.output_dir / "fixture-stderr.log").open("ab")
        command = [self.args.node, str(script), "--python", str(self.args.python),
                   "--manifest", str(self.args.manifest), "--output-dir", str(self.args.output_dir),
                   "--panel-build-dir", str(self.args.panel_build_dir), "--resume", str(resume).lower()]
        self.process = subprocess.Popen(command, cwd=self.args.output_dir, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1,
                                        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp")})
        self.info = self.receive(40)
        assert self.info.get("ready"), self.info
        return self.info

    def command(self, op, *, deadline=120, **fields):
        self.sequence += 1
        self.process.stdin.write(json.dumps({"id": self.sequence, "op": op, **fields}) + "\n")
        self.process.stdin.flush()
        result = self.receive(deadline)
        assert result.get("id") == self.sequence and "error" not in result, result
        return result["value"]

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            result = {"stopped": False, "outcome": "unconfirmed"}
            try:
                result = self.command("shutdown", deadline=20)
            except (AssertionError, OSError, RuntimeError, TimeoutError) as exc:
                result["failure_type"] = type(exc).__name__
                self.process.terminate()
            try:
                self.process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            result["fixture_exit_code"] = self.process.returncode
            self.cleanup.append(result)
        elif self.process is not None:
            # A fixture that exited before acknowledging shutdown cannot prove
            # its adapter/MCP descendants stopped, even with exit status zero.
            self.cleanup.append({"stopped": False, "outcome": "unconfirmed",
                                 "failure_type": "exited_before_shutdown_acknowledgment",
                                 "fixture_exit_code": self.process.returncode})
        if self.process:
            self.process.stdin.close()
            self.process.stdout.close()
        if self.log:
            self.log.close()
        self.process, self.log = None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True, help="Installed exact-release Python, launched with -I -B")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--panel-build-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--browser", default=shutil.which("chromium"))
    parser.add_argument("--node", default=shutil.which("node"))
    parser.add_argument("--require-matching-release", action="store_true")
    args = parser.parse_args()
    for name in ("python", "manifest", "panel_build_dir", "output_dir"):
        setattr(args, name, getattr(args, name).absolute())
    if not args.node or not args.browser:
        parser.error("Existing Node and Chromium executables are required; no packages are installed")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    if shutil.disk_usage(args.output_dir).free < 1024**3:
        raise RuntimeError("Insufficient temporary space: reserve at least 1 GiB")
    report = {"schema": "odsp.panel-browser-journey.v1", "status": "RUNNING", "checks": [], "screenshots": [],
              "scope": "local_synthetic_browser_RS256_BFF_gateway_Worker_installed_MCP",
              "storage": "repository_DiskStorage_FileBucket_substitutes_not_Cloudflare_qualification",
              "hosted_authentication": "not_qualified", "physical_qualification": "not_performed",
              "accessibility_scope": "structural_keyboard_reflow_reduced_motion_checks_not_complete_WCAG_audit"}
    fixture, browser, context = Fixture(args), None, None
    errors, traffic, navigations = [], [], []
    report["downloaded_responses"] = []

    def save():
        (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    def check(name, **evidence):
        report["checks"].append({"id": name, "passed": True, **evidence})
        save()

    try:
        info = fixture.start()
        report["backend_release"] = info["release"]
        report["panel_source"] = json.loads((args.panel_build_dir / "build-receipt.json").read_text())["source"]
        if args.require_matching_release:
            assert report["panel_source"]["source_commit"] == info["release"]["source_commit"]
            assert report["panel_source"]["source_tree"] == info["release"]["source_tree"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser, headless=True,
                                                 args=["--no-sandbox", "--disable-dev-shm-usage"])
            report["browser_version"] = browser.version

            def session(principal="panel-alpha"):
                ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
                assertion = fixture.command("session", principal=principal)["assertion"]
                ctx.add_cookies([{"name": "CF_Authorization", "value": assertion, "url": fixture.info["url"],
                                  "httpOnly": True, "sameSite": "Strict"}])
                page = ctx.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda req: traffic.append({"method": req.method, "path": req.url.split("/", 3)[-1]}))
                page.on("framenavigated", lambda frame: navigations.append(frame.url) if frame == page.main_frame else None)
                return ctx, page

            anonymous = browser.new_context()
            before = fixture.command("snapshot")
            response = anonymous.request.get(info["url"] + "/")
            assert response.status in (401, 403)
            anonymous.close()
            after = fixture.command("snapshot")
            assert before["metrics"]["gateway_requests"] == after["metrics"]["gateway_requests"]
            assert after["metrics"]["container_starts"] == 0
            check("unauthenticated_assets_refused_without_backend_or_MCP_start")

            context, page = session()
            url = fixture.info["url"]
            page.goto(url)
            page.wait_for_selector("#connect-form")
            page.wait_for_timeout(100)
            assert fixture.command("snapshot")["metrics"]["adapter_transactions"] == 0
            before = fixture.command("snapshot")
            with page.expect_response(lambda r: r.url.endswith("/api/readiness")) as received:
                page.locator("#connect-form button[type=submit]").click()
            readiness = received.value.json()
            page.wait_for_function("() => !document.querySelector('#open-session').disabled")
            after = fixture.command("snapshot")
            assert readiness["ready"] is False and readiness["reason"] == "host_not_running"
            assert before["control"] == after["control"] and before["committed"] == after["committed"]
            assert before["objects"] == after["objects"] == []
            assert after["metrics"]["container_starts"] == after["metrics"]["adapter_transactions"] == 0
            assert after["state"] == []
            check("authenticated_readiness_does_not_wake_or_write_checkpoint")
            assert fixture.command("strict-gateway-probe")["status"] == 403
            check("unchanged_gateway_still_rejects_HTTP_or_Origin")

            def open_session(target):
                target.locator("#open-session").click()
                target.wait_for_function("() => document.querySelectorAll('#tool-list .tool-item').length > 0 && !document.querySelector('#tool-list .tool-item').disabled",
                                         timeout=45000)

            open_session(page)
            snapshot = fixture.command("snapshot")
            assert snapshot["metrics"]["container_starts"] == 1
            assert not snapshot["metrics"]["process_observation_failures"]
            assert snapshot["metrics"]["mcp_process_ids"]
            assert snapshot["committed"] and snapshot["pending"] is None
            check("explicit_session_crosses_real_BFF_gateway_Worker_installed_MCP", subprocesses=len(snapshot["metrics"]["mcp_process_ids"]))

            def downloaded_receipt(target, response, stage):
                # The production UI downloads its exact received Uint8Array.
                # Verify it against the fixture-observed BFF wire bytes without
                # relying on Chromium DevTools' evictable response-body cache.
                target.wait_for_function("() => !document.querySelector('#arguments').disabled || !document.querySelector('#hold-warning').hidden", timeout=95000)
                target.wait_for_function("() => !document.querySelector('#download').disabled")
                with target.expect_download() as saved:
                    target.locator("#download").click()
                name = f"response-{len(report['downloaded_responses']) + 1:03}.json"
                saved.value.save_as(args.output_dir / name)
                raw = (args.output_dir / name).read_bytes()
                wire = fixture.command("last-response")
                assert wire["path"] == "/api/calls" and wire["status"] == response.status
                assert len(raw) == wire["bytes"] <= 32 * 1024 * 1024
                assert hashlib.sha256(raw).hexdigest() == wire["sha256"]
                assert target.locator("#result").text_content().encode("utf-8") == raw
                report["downloaded_responses"].append({"stage": stage, "file": name, **wire})
                save()
                return json.loads(raw)

            def call(target, name, arguments=None, *, raw=None, write=False, expect_error=False):
                target.locator("#tool-list .tool-item").filter(has_text=name).first.click()
                text = raw if raw is not None else json.dumps(arguments or {}, ensure_ascii=False)
                target.locator("#arguments").fill(text)
                if write:
                    assert target.locator("#call").is_disabled()
                    target.locator("#confirm-write").check()
                    target.locator("#arguments").fill(text + "\n")
                    assert target.locator("#call").is_disabled()
                    target.locator("#confirm-write").check()
                navigation_count = len(navigations)
                with target.expect_response(lambda r: r.url.endswith("/api/calls"), timeout=95000) as returned:
                    target.locator("#call").click()
                response = returned.value
                value = downloaded_receipt(target, response, name)
                assert len(navigations) == navigation_count, "Tool call unexpectedly navigated the page"
                if expect_error:
                    return response.status, value
                assert response.ok, value
                assert value["retention"]["committed"] is True, value
                assert value["retention"]["transaction_id"]
                assert value["raw_json"] and json.loads(value["raw_json"]) == value["data"]
                target.wait_for_function("""id => {try{return JSON.parse(document.querySelector('#result').textContent)
                    .retention.transaction_id===id;}catch{return false;}}""", arg=value["retention"]["transaction_id"])
                rendered = json.loads(target.locator("#result").inner_text())
                assert rendered["retention"] == value["retention"]
                assert not value["data"].get("isError"), value
                return value

            template = call(page, "ncp_plan_template", {"baseline": "baseline"})
            # The exact template string preserves scientific floats through JS.
            plan_response = call(page, "ncp_plan_create", raw=template["plan_arguments_json"], write=True)
            plan = plan_response["data"]["structuredContent"]["data"]
            digest = {"plan_id": plan["plan_id"], "expected_digest": plan["digest"]}
            assert plan["admitted"] is False
            call(page, "ncp_plan_admit", digest, write=True)
            run_response = call(page, "ncp_run_submit", {**digest, "attempt": "browser-explicit-synthetic-attempt"}, write=True)
            run = run_response["data"]["structuredContent"]["data"]
            assert run["state"] == "completed" and len(run["result"]["execution_ids"]) == 7
            inspected = call(page, "ncp_run_get", {"run_id": run["run_id"]})
            assert inspected["data"]["structuredContent"]["data"] == run
            report["run_id"], report["execution_ids"] = run["run_id"], run["result"]["execution_ids"]
            check("browser_template_create_admit_submit_get_preserves_exact_receipts_and_seven_results")
            check("editing_confirmed_arguments_revokes_confirmation")

            # Cross-owner read is exercised by the real facade. No scientific
            # execution or foreign result is returned to the second principal.
            beta_context, beta = session("panel-beta")
            beta.goto(url)
            beta.locator("#connect-form button[type=submit]").click()
            beta.wait_for_function("() => !document.querySelector('#open-session').disabled")
            open_session(beta)
            status, denied = call(beta, "ncp_run_get", {"run_id": run["run_id"]}, expect_error=True)
            assert status == 200 and denied["data"]["isError"]
            assert denied["data"]["structuredContent"]["data"] is None
            assert run["result"]["execution_ids"][0] not in json.dumps(denied)
            beta_context.close()
            check("different_principal_cannot_read_retained_run_or_receive_execution_data")

            before = fixture.command("snapshot")
            fixture.command("policy", mode="disable-alpha")
            refused = context.request.get(url + "/api/readiness", headers={"X-ODSP-Panel-Request": "1"})
            assert refused.status in (401, 403)
            after = fixture.command("snapshot")
            assert before["metrics"]["gateway_requests"] == after["metrics"]["gateway_requests"]
            assert before["metrics"]["adapter_transactions"] == after["metrics"]["adapter_transactions"]
            fixture.command("policy", mode="enable-alpha")
            cross_origin = context.request.post(url + "/api/calls", headers={"Origin": "https://foreign.invalid", "X-ODSP-Panel-Request": "1"},
                                                data={"name": "ncp_run_get", "arguments": {"run_id": run["run_id"]}})
            assert cross_origin.status == 403
            check("current_access_denial_and_cross_origin_requests_never_launch_backend_work")

            # Stop the entire Node Worker process and actual adapter, then start
            # fresh processes with the same saved SQLite/R2/owner/generation.
            prior = fixture.command("snapshot")
            prior_pid = fixture.info["pid"]
            context.close(); context = None
            fixture.stop()
            fixture.start(resume=True)
            assert fixture.info["pid"] != prior_pid
            context, page = session()
            url = fixture.info["url"]
            page.goto(url)
            page.locator("#connect-form button[type=submit]").click()
            page.wait_for_function("() => !document.querySelector('#open-session').disabled")
            stopped = fixture.command("snapshot")
            assert stopped["committed"] == prior["committed"] and not stopped["adapter_running"]
            open_session(page)
            reopened = call(page, "ncp_run_get", {"run_id": run["run_id"]})
            assert reopened["data"]["structuredContent"]["data"] == run
            resumed = fixture.command("snapshot")
            assert all(row["tool"] != "ncp_run_submit" for row in resumed["metrics"]["transactions"])
            check("actual_Worker_and_adapter_process_restart_reopens_exact_run_without_resubmission")

            # Responsive screenshots use actual long retained identities/results.
            for width in (320, 375, 768, 1440):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(50)
                overflow = page.evaluate("Math.max(document.body.scrollWidth,document.documentElement.scrollWidth)-document.documentElement.clientWidth")
                assert overflow <= 1, {"width": width, "overflow": overflow}
                name = f"panel-{width}.png"
                page.screenshot(path=args.output_dir / name, full_page=True)
                report["screenshots"].append(name)
            check("responsive_320_375_768_1440_without_document_overflow")
            page.set_viewport_size({"width": 1440, "height": 1000})
            original_ratio = page.evaluate("window.devicePixelRatio")
            original_css_width = page.evaluate("window.innerWidth")
            for _ in range(4):
                page.keyboard.press("Control+Equal")
            page.wait_for_timeout(50)
            zoom = page.evaluate("window.devicePixelRatio") / original_ratio
            width_ratio = original_css_width / page.evaluate("window.innerWidth")
            method = "Chromium_keyboard_zoom"
            native_zoom = 1.95 <= zoom <= 2.05 and 1.95 <= width_ratio <= 2.05
            if not native_zoom:
                page.keyboard.press("Control+Digit0")
                page.evaluate("document.documentElement.style.zoom='2'")
                assert float(page.evaluate("getComputedStyle(document.documentElement).zoom")) == 2
                method = "CSS_zoom_2_layout_scale"
            overflow = page.evaluate("Math.max(document.body.scrollWidth,document.documentElement.scrollWidth)-document.documentElement.clientWidth")
            assert overflow <= 1, {"zoom_method": method, "overflow": overflow}
            page.screenshot(path=args.output_dir / "panel-200-percent.png", full_page=True)
            report["screenshots"].append("panel-200-percent.png")
            page.keyboard.press("Control+Digit0")
            page.evaluate("document.documentElement.style.zoom=''")
            if native_zoom:
                check("native_Chromium_200_percent_page_zoom", method=method, measured_dpr_ratio=zoom, measured_css_width_ratio=width_ratio)
            else:
                check("200_percent_CSS_layout_scale_only", method=method)
                report["remaining_gaps"] = [{"criterion": "native_browser_200_percent_page_zoom", "status": "UNVERIFIED",
                    "reason": "Chromium keyboard zoom did not produce a measured 2x device-pixel ratio. CSS scaling is separate layout evidence."}]
            audit = page.evaluate("""() => ({
                language:document.documentElement.lang,
                h1:document.querySelectorAll('h1').length,
                main:document.querySelectorAll('main').length,
                unlabeledControls:[...document.querySelectorAll('input,textarea,select')].filter(e=>e.type!=='hidden'&&!e.labels?.length&&!e.getAttribute('aria-label')&&!e.getAttribute('aria-labelledby')).map(e=>e.id),
                unnamedButtons:[...document.querySelectorAll('button')].filter(e=>!e.textContent.trim()&&!e.getAttribute('aria-label')).length,
                statusRegions:document.querySelectorAll('[role=status],[aria-live]').length,
                readinessText:document.querySelector('#readiness-state').textContent.trim(),
                receiptText:document.querySelector('#receipt-state').textContent.trim(),
                reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches
            })""")
            assert audit["language"] and audit["h1"] == 1 and audit["main"] == 1
            assert audit["unlabeledControls"] == [] and audit["unnamedButtons"] == 0
            assert audit["statusRegions"] and audit["readinessText"] and audit["receiptText"] and audit["reducedMotion"]
            page.locator("#search").focus()
            page.keyboard.type("ncp_run_get")
            page.locator("#tool-list .tool-item").first.focus()
            page.keyboard.press("Enter")
            page.locator("#arguments").focus()
            page.keyboard.press("Control+A")
            page.keyboard.type(json.dumps({"run_id": run["run_id"]}))
            page.locator("#call").focus()
            assert page.locator("#call").evaluate("e=>e===document.activeElement")
            focus_style = page.locator("#call").evaluate("""e=>{const s=getComputedStyle(e);return {
                outline:s.outlineStyle, width:parseFloat(s.outlineWidth), shadow:s.boxShadow};}""")
            assert focus_style["outline"] != "none" and focus_style["width"] >= 1 or focus_style["shadow"] != "none", focus_style
            with page.expect_response(lambda r:r.url.endswith("/api/calls")) as keyboard_response:
                page.keyboard.press("Enter")
            keyboard_receipt = downloaded_receipt(page, keyboard_response.value, "keyboard_ncp_run_get")
            assert keyboard_receipt["retention"]["committed"] is True
            (args.output_dir / "accessibility-tree.txt").write_text(page.locator("body").aria_snapshot())
            check("keyboard_operation_labels_live_status_noncolour_state_and_reduced_motion", audit=audit, focus=focus_style)

            # A real completed adapter response followed by a storage publication
            # fault must remain unknown, retained, fenced, and latched after reload.
            fixture.command("fail-publication")
            status, unconfirmed = call(page, "ncp_run_get", {"run_id": run["run_id"]}, expect_error=True)
            assert status >= 400 and unconfirmed.get("error")
            page.wait_for_function("() => !document.querySelector('#hold-warning').hidden")
            before_reload = fixture.command("snapshot")
            assert before_reload["pending"] is not None
            assert any(item["path"].startswith("state/returned/") for item in before_reload["state"])
            count = before_reload["metrics"]["adapter_transactions"]
            page.reload()
            page.wait_for_selector("#hold-warning", state="visible")
            assert page.locator("#call").is_disabled()
            assert page.locator("#open-session").is_disabled()
            with page.expect_response(lambda r:r.url.endswith("/api/readiness")) as received:
                page.locator("#refresh-readiness").click()
            fenced = received.value.json()
            assert fenced["fenced"] is True and fenced["ready"] is False
            after_reload = fixture.command("snapshot")
            assert after_reload["metrics"]["adapter_transactions"] == count
            assert after_reload["pending"] == before_reload["pending"]
            page.screenshot(path=args.output_dir / "panel-unknown-held.png", full_page=True)
            report["screenshots"].append("panel-unknown-held.png")
            check("publication_failure_preserves_evidence_and_unknown_fence_across_frontend_reload")
            report["final_fixture_snapshot"] = after_reload
            assert not errors, errors
            # Browser-visible storage/DOM must never contain backend credentials
            # or an editable authority selector. The Access cookie is HttpOnly.
            assert "CF_Authorization" not in page.evaluate("document.cookie")
            assert page.locator('input[type=password],input[name=token],input[name=principal]').count() == 0
            check("credentials_stay_out_of_browser_UI_and_script_cookie_access")
            report["status"] = "PARTIAL" if report.get("remaining_gaps") else "PASS"
    except BaseException as exc:
        report["status"] = "FAIL"
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)[:3000],
                             "location": traceback.format_tb(exc.__traceback__)[-1][-1500:]}
        save()
        try:
            report["failure_fixture_snapshot"] = fixture.command("snapshot", deadline=10)
            save()
        except Exception:
            pass
        if context:
            try:
                context.pages[-1].screenshot(path=args.output_dir / "failure.png", full_page=True)
                report["screenshots"].append("failure.png")
            except Exception:
                pass
        raise
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        fixture.stop()
        report["fixture_cleanup"] = fixture.cleanup
        if any(not item.get("stopped") or item.get("owned_survivors") != 0 or
               item.get("fixture_exit_code") != 0 or item.get("process_observation_failures")
               for item in fixture.cleanup):
            report["status"] = "FAIL"
            report["cleanup_failure"] = "Fixture shutdown did not confirm all owned processes stopped."
        report["page_errors"] = errors
        report["browser_requests"] = traffic
        report["page_navigations"] = navigations
        save()
        print(json.dumps({"status": report["status"], "checks": len(report["checks"]),
                          "report": str(args.output_dir / "report.json")}), flush=True)
    if report["status"] == "PARTIAL":
        raise SystemExit(2)
    if report["status"] == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
