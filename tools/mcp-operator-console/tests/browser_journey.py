"""Real Chromium -> local HTTP console -> MCP host -> retained synthetic run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright
from ciw.agent_mcp import demo_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--browser")
    args = parser.parse_args()
    output = args.output_dir.absolute()
    output.mkdir(parents=True, exist_ok=False)
    console = Path(__file__).resolve().parents[1]
    profile = demo_config(output / "profile")
    token = secrets.token_urlsafe(32)
    token_file = output / "token"
    token_file.touch(mode=0o600)
    token_file.write_text(token, encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    checks, errors = [], []
    with (output / "http.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([
            sys.executable, str(console / "console.py"), "--python", sys.executable,
            "--profile", str(profile), "--state-dir", str(output / "state"),
            "--token-file", str(token_file), "--port", str(port),
        ], cwd=console, stdout=log, stderr=log)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 30
            while True:
                try:
                    with opener.open(url, timeout=1):
                        break
                except urllib.error.URLError:
                    if time.monotonic() >= deadline or process.poll() is not None:
                        raise RuntimeError("Local console startup failed; inspect http.log")
                    time.sleep(0.05)
            with sync_playwright() as playwright:
                options = {"headless": True, "args": ["--no-sandbox"]}
                if args.browser:
                    options["executable_path"] = args.browser
                browser = playwright.chromium.launch(**options)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(url)
                    assert page.locator("#call").is_disabled()
                    page.locator("#token").fill(token)
                    page.locator("#connect-form button").click()
                    page.wait_for_function("() => document.querySelector('#tools-metric').textContent === '14'")
                    assert page.locator("#token").input_value() == ""
                    checks.append("authenticated_browser_discovers_14_real_MCP_tools")

                    def call(name, arguments, *, write=False):
                        page.locator("#tool-list button").filter(has_text=name).click()
                        page.locator("#arguments").fill(json.dumps(arguments))
                        if write:
                            assert page.locator("#call").is_disabled()
                            page.locator("#confirm-write").check()
                            # Editing an already confirmed call must invalidate consent.
                            page.locator("#arguments").fill(json.dumps(arguments, indent=2))
                            assert page.locator("#call").is_disabled()
                            page.locator("#confirm-write").check()
                        with page.expect_response(lambda response: response.url == url + "/api/calls") as received:
                            page.locator("#call").click()
                        response = received.value
                        assert response.ok
                        result = response.json()["data"]
                        assert not result.get("isError"), result
                        page.wait_for_function("() => !document.querySelector('#arguments').disabled")
                        assert json.loads(page.locator("#result").inner_text()) == result
                        return result["structuredContent"]["data"]

                    graph = call("ncp_plan_template", {"baseline": "baseline"})["graph"]
                    plan = call("ncp_plan_create", {"source": "source", "baseline": "baseline", "graph": graph}, write=True)
                    assert plan["admitted"] is False
                    checks.append("plan_creation_requires_fresh_confirmation_and_remains_unadmitted")
                    digest = {"plan_id": plan["plan_id"], "expected_digest": plan["digest"]}
                    call("ncp_plan_admit", digest, write=True)
                    run = call("ncp_run_submit", {**digest, "attempt": "browser-journey-001"}, write=True)
                    deadline = time.monotonic() + 30
                    while run["state"] in {"queued", "running"}:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Synthetic run did not complete")
                        run = call("ncp_run_get", {"run_id": run["run_id"]})
                    assert run["state"] == "completed" and run["result"]["execution_ids"]
                    assert page.locator("#runs-metric").inner_text() == "1"
                    checks.append("explicit_admission_submission_and_retained_execution_inspection")
                    page.locator("#resource-uri").fill(plan["uri"])
                    with page.expect_response(lambda response: response.url == url + "/api/resources") as received:
                        page.locator("#read-resource").click()
                    resource = received.value.json()["data"]
                    assert json.loads(resource["contents"][0]["text"])["plan_id"] == plan["plan_id"]
                    checks.append("browser_reads_the_same_retained_plan_resource")
                    assert not errors, errors
                    version = browser.version
                finally:
                    browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            token_file.unlink(missing_ok=True)
    report = {"status": "PASS", "browser_version": version, "checks": checks,
              "page_errors": errors, "scope": "local synthetic browser/HTTP/MCP integration",
              "remote_transport": "not_qualified", "physical_qualification": "not_performed"}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
