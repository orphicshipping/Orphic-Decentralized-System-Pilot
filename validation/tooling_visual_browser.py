"""Chromium regression checks for the offline tooling map and capability inspector.

Navigate the generated file itself, retain screenshots and a report, and never
bind or execute scientific providers. Requires the optional Playwright package.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright

from ciw import operator_catalog
from ciw.tooling_visualizer import render_html


def searchable(row):
    """Catalog fields exposed by the workflow search, including provider roles."""
    return " ".join(str(value) for value in (
        row["workflow"], row.get("operation_id", ""), row.get("role", ""),
        *row.get("capabilities", []), *row.get("instrument_highlights", []),
        *row.get("providers", {}), *row.get("external_bindings", {}),
    )).lower()


def matches_query(row, query):
    """Match all punctuation-insensitive search tokens, independently of order."""
    tokens = re.findall(r"[a-z0-9]+", query.lower())
    haystack = " ".join(re.findall(r"[a-z0-9]+", searchable(row)))
    return all(token in haystack for token in tokens)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path, help="New directory for generated files, screenshots and report")
    parser.add_argument("--chromium", help="Optional Chromium/Edge executable; otherwise use Playwright's installed Chromium")
    args = parser.parse_args(argv)
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    catalog = operator_catalog.catalog()
    original = deepcopy(catalog)
    html = root / "tooling.html"
    html.write_bytes(render_html())
    workflows = {row["workflow"]: row for row in catalog["workflows"]}
    operations = catalog["builtin_providers"]["operations"]
    commands = {row["command"]: row for row in catalog["commands"]}
    checks, errors, external = [], [], []
    browser_version = None
    failure = None

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    def tab(page, name):
        target = page.locator('[data-inspector-tab="' + name + '"]')
        target.click()
        expect(target).to_have_attribute("aria-pressed", "true")

    def source_matches(page, expected, label):
        tab(page, "source")
        source = page.locator("#inspector-content .source-record pre")
        check(label, json.loads(source.text_content()) == expected)
        tab(page, "overview")

    def settle_map(page):
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")

    def map_geometry(page):
        return page.evaluate(r"""() => {
            const map = document.querySelector('#map'), svg = document.querySelector('#connections');
            const center = document.querySelector('#map .selected'), bounds = map.getBoundingClientRect();
            const requirements = [...document.querySelectorAll('#map-columns .map-node[data-requirement-id]')];
            const allRequirementsVisible = requirements.every(node => {
                const rect = node.getBoundingClientRect();
                return rect.top >= bounds.top && rect.bottom <= bounds.bottom && rect.left >= bounds.left && rect.right <= bounds.right;
            });
            const errors = [...svg.querySelectorAll('path')].map(path => {
                const node = document.querySelector('#map-columns [data-map-link="' + path.dataset.mapLink + '"]');
                const selected = center.getBoundingClientRect(), other = node.getBoundingClientRect(), left = other.left < selected.left;
                const start = path.getPointAtLength(0).matrixTransform(path.getScreenCTM());
                const end = path.getPointAtLength(path.getTotalLength()).matrixTransform(path.getScreenCTM());
                const expectedStart = left ? [other.right, other.top + other.height / 2] : [selected.right, selected.top + selected.height / 2];
                const expectedEnd = left ? [selected.left, selected.top + selected.height / 2] : [other.left, other.top + other.height / 2];
                return Math.max(Math.abs(start.x - expectedStart[0]), Math.abs(start.y - expectedStart[1]), Math.abs(end.x - expectedEnd[0]), Math.abs(end.y - expectedEnd[1]));
            });
            return {allRequirementsVisible, requirementCount: requirements.length, maxEndpointError: Math.max(0, ...errors), scrollTop: map.scrollTop};
        }""")

    def observe(page):
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("request", lambda request: external.append(request.url)
                if request.url.startswith(("http://", "https://", "ws://", "wss://")) else None)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, **(
                {"executable_path": args.chromium} if args.chromium else {}))
            browser_version = browser.version
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            observe(page)
            page.goto(html.as_uri())
            expect(page.locator('#tool-list [data-tool-id="calibrated-observable"]')).to_have_attribute("aria-pressed", "true")
            check("Generated file loads with all workflow declarations", page.locator("#tool-list .tool").count() == len(workflows))
            check("Displayed catalog matches exact Python catalog", page.evaluate(
                'JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(document.getElementById("tooling-data").textContent.trim()), c => c.charCodeAt(0)))).catalog'
            ) == catalog)
            for element, expected in (("workflow-count", len(workflows)), ("operation-count", len(operations)), ("command-count", len(commands))):
                check("Catalog count: " + element, page.locator("#" + element).inner_text() == str(expected))
            default = workflows["calibrated-observable"]
            required = len(default["providers"]) + len(default["external_bindings"])
            check("Default workflow shows every declared requirement", page.locator("#map .map-node[data-requirement-id]").count() == required)
            expect(page.locator("#inspector-content")).to_contain_text(default["operation_id"])
            check("Default operation identity is visible", True)
            for width in (1440, 1024):
                page.set_viewport_size({"width": width, "height": 1050})
                settle_map(page)
                geometry = map_geometry(page)
                check(f"All {required} default provider cards fit the visible map at {width}px", geometry["allRequirementsVisible"] and geometry["requirementCount"] == required)
                check(f"Connectors meet their map nodes at {width}px", geometry["maxEndpointError"] < 3)
            page.set_viewport_size({"width": 1440, "height": 1050})
            settle_map(page)
            check("Inspector starts on Overview", page.locator('[data-inspector-tab="overview"]').get_attribute("aria-pressed") == "true")
            source_matches(page, default, "Workflow Source preserves its exact declaration")
            tab(page, "bindings")
            check("Bindings tab shows every declared requirement", page.locator("#inspector-content .requirement").count() == required)
            tab(page, "commands")
            expect(page.locator("#inspector-content .command-box code").last).to_have_text(default["execution_entry"])
            check("Workflow Commands preserves the catalog usage entry", True)
            tab(page, "overview")
            page.screenshot(path=str(root / "tooling-initial.png"), full_page=True)

            expect(page.locator("html")).to_have_attribute("data-theme", "dark")
            dark_background = page.locator("body").evaluate("element => getComputedStyle(element).backgroundColor")
            page.locator("#theme-toggle").click()
            expect(page.locator("html")).to_have_attribute("data-theme", "light")
            expect(page.locator("#theme-toggle")).to_have_attribute("aria-pressed", "true")
            check("Light theme changes the page background", page.locator("body").evaluate("element => getComputedStyle(element).backgroundColor") != dark_background)
            page.screenshot(path=str(root / "tooling-light.png"), full_page=True)
            page.locator("#theme-toggle").click()
            expect(page.locator("html")).to_have_attribute("data-theme", "dark")
            expect(page.locator("#theme-toggle")).to_have_attribute("aria-pressed", "false")
            check("Theme toggle returns to dark", True)

            page.locator("#focus-map").click()
            expect(page.locator(".workspace")).to_have_class(re.compile(r"\bmap-focused\b"))
            expect(page.locator("#focus-map")).to_have_attribute("aria-pressed", "true")
            check("Map focus control enters focused layout", True)
            page.screenshot(path=str(root / "tooling-focus.png"), full_page=True)

            page.locator('#map .map-node[data-requirement-id="tbrt"]').click()
            expect(page.locator("#inspector-content h3")).to_have_text("tbrt")
            expect(page.locator("#inspector-content")).to_contain_text(default["providers"]["tbrt"]["revision"])
            check("Provider inspector retains the full catalog pin", True)
            check("Inspecting a provider exits focused map layout", "map-focused" not in (page.locator(".workspace").get_attribute("class") or ""))
            expect(page.locator("#focus-map")).to_have_attribute("aria-pressed", "false")
            expect(page.locator('#connections path[data-requirement-id="tbrt"].is-active')).to_have_count(1)
            check("Inspected provider activates its matching connector", True)
            check("Provider inspection highlights its map node", page.locator('#map .map-node[data-requirement-id="tbrt"]').get_attribute("aria-pressed") == "true")
            page.screenshot(path=str(root / "tooling-provider.png"), full_page=True)
            page.locator("#map .map-node.selected").click()
            expect(page.locator("#selection-kind")).to_have_text("Workflow")
            check("Center-node click clears provider selection and highlighting", page.locator('#map .map-node[data-requirement-id][aria-pressed="true"]').count() == 0)
            page.locator('#map .map-node[data-requirement-id="tbrt"]').click()
            page.get_by_role("button", name="← Back to tool", exact=True).click()
            expect(page.locator("#selection-kind")).to_have_text("Workflow")
            check("Back restores the workflow inspector", True)
            page.locator('#map .map-node[data-requirement-id="tbrt"]').focus()
            expect(page.locator('#connections path[data-requirement-id="tbrt"].is-active')).to_have_count(1)
            check("Keyboard focus activates the matching provider connector", True)
            page.locator("#search").focus()
            page.mouse.move(0, 0)
            expect(page.locator("#connections path.is-active")).to_have_count(0)
            check("Provider connector deactivates after selection and focus leave", True)
            page.locator('#tool-list [data-tool-id="calibrated-observable"]').focus()
            page.locator('#tool-list [data-tool-id="calibrated-observable"]').press("Enter")
            check("Keyboard activation preserves focus on the selected tool", page.evaluate('document.activeElement.dataset.toolId') == "calibrated-observable")

            page.locator("#focus-map").click()
            page.locator("#focus-map").focus()
            page.keyboard.press("/")
            expect(page.locator("#search")).to_be_focused()
            check("Slash shortcut focuses tool search", True)
            check("Slash reveals search by exiting focused map layout", "map-focused" not in (page.locator(".workspace").get_attribute("class") or ""))
            page.locator("#search").fill("calibrated observable")
            normalized = [row for row in catalog["workflows"] if matches_query(row, "calibrated observable")]
            check("Multiword search matches punctuation-normalized workflow names", page.locator("#tool-list .tool").count() == len(normalized) and "calibrated-observable" in [row["workflow"] for row in normalized])
            expect(page.locator('#tool-list [data-tool-id="calibrated-observable"]')).to_be_visible()
            page.locator("#search").fill("tbrt calibr")
            check("Search requires every provider and capability token to match", page.locator("#tool-list .tool").count() == len([row for row in catalog["workflows"] if matches_query(row, "tbrt calibr")]))
            page.keyboard.press("Escape")
            expect(page.locator("#search")).to_have_value("")
            check("Escape clears an active search and restores all workflows", page.locator("#tool-list .tool").count() == len(workflows))
            page.locator("#focus-map").click()
            expect(page.locator("#focus-map")).to_have_attribute("aria-pressed", "true")
            page.keyboard.press("Escape")
            check("Escape exits focused map after the search is cleared", "map-focused" not in (page.locator(".workspace").get_attribute("class") or ""))
            expect(page.locator("#focus-map")).to_be_focused()
            page.locator("#search").fill("calibrated")
            page.locator("#search").press("End")
            page.keyboard.press("/")
            expect(page.locator("#search")).to_have_value("calibrated/")
            check("Slash remains ordinary text while editing search", True)
            page.locator("#clear-search").click()
            page.locator('#tool-list [data-tool-id="calibrated-observable"]').click()
            page.locator('#map .map-node[data-requirement-id="tbrt"]').click()
            page.keyboard.press("Escape")
            expect(page.locator("#selection-kind")).to_have_text("Workflow")
            check("Escape returns from provider inspection to its workflow", True)

            page.locator("#search").fill("thermal")
            thermal = [row for row in catalog["workflows"] if "thermal" in searchable(row)]
            check("Thermal search returns precisely matching declarations", page.locator("#tool-list .tool").count() == len(thermal))
            page.locator('#tool-list [data-tool-id="thermal-observer"]').click()
            expect(page.locator("#inspector-content .status-label")).to_have_text("Available in an unbound Session")
            check("Built-in workflow availability matches the unbound catalog", workflows["thermal-observer"]["available"] is True)
            check("Built-in workflow does not invent provider requirements", page.locator("#map .map-node[data-requirement-id]").count() == 0)
            page.locator("#binding-filter").select_option("external")
            check("Combined external filter respects search", page.locator("#tool-list .tool").count() == len([row for row in thermal if row["available"] is False]))
            expect(page.locator("#map-title")).to_have_text("No matching tools")
            check("Empty results clear the relationship map", page.locator("#map .map-node").count() == 0)
            page.locator("#clear-search").click()
            check("Clear restores every workflow and resets its filter", page.locator("#tool-list .tool").count() == len(workflows) and page.locator("#binding-filter").input_value() == "all")

            session_only = next(row for row in catalog["workflows"] if row["scientific_cli"] is False)
            page.locator('#tool-list [data-tool-id="' + session_only["workflow"] + '"]').click()
            tab(page, "commands")
            expect(page.locator("#inspector-content .command-box code").last).to_have_text("ciw serve --help")
            check("Session-only workflow displays a runnable help command", True)

            page.locator('[data-scope="operations"]').click()
            check("Built-in operation list matches catalog", page.locator("#tool-list .tool").count() == len(operations))
            check("Workflow filter is hidden in operation scope", page.locator("#binding-filter").is_hidden() and page.locator(".filter-label").is_hidden())
            operation_id = next(iter(operations))
            operation = operations[operation_id]
            page.locator('#tool-list [data-tool-id="' + operation_id + '"]').click()
            for name, port in operation["outputs"].items():
                expect(page.locator("#inspector-content")).to_contain_text(port["type"]["schema"])
                check("Output schema preserved: " + name, True)
                for key in ("unit", "frame"):
                    expected = "Not declared" if port["type"][key] is None else str(port["type"][key])
                    fact = page.locator("#inspector-content .fact").filter(has=page.get_by_text(key.capitalize(), exact=True))
                    expect(fact.locator("dd")).to_have_text(expected)
                    check("Unknown output " + key + " remains explicitly undeclared", True)
            source_matches(page, operation, "Operation Source preserves its exact provider contract")
            tab(page, "commands")
            expect(page.locator("#inspector-content .command-box code")).to_have_text("net capabilities " + operation["capabilities"][0] + " --json")
            check("Operation inspector shows the existing capability CLI syntax", True)
            tab(page, "overview")
            page.screenshot(path=str(root / "tooling-operation.png"), full_page=True)

            page.locator('[data-scope="commands"]').click()
            check("Command scope includes all installed surfaces", page.locator("#tool-list .tool").count() == len(commands))
            page.locator("#search").fill("board")
            page.locator('#tool-list [data-tool-id="net board"]').click()
            source_matches(page, commands["net board"], "Command Source preserves its exact catalog entry")
            tab(page, "commands")
            expect(page.locator("#inspector-content .command-box code")).to_have_text(commands["net board"]["help_command"])
            expect(page.locator("#map-title")).to_have_text("net board")
            check("Board command search leads to its exact help command", True)

            # Controlled browser API outcomes exercise offline permission failures
            # without reading or writing the operator's actual system clipboard.
            page.evaluate("""() => {
                window.clipboardAttempts = [];
                Object.defineProperty(navigator, "clipboard", {configurable: true, value: {
                    writeText: text => {window.clipboardAttempts.push(text); return new Promise(resolve => {window.completeCopy = resolve;});}
                }});
            }""")
            copy_button = page.locator("#inspector-content .copy-command")
            copy_button.focus()
            copy_button.press("Enter")
            expect(copy_button).to_have_attribute("aria-disabled", "true")
            check("Pending clipboard copy preserves keyboard focus", copy_button.evaluate("element => element === document.activeElement"))
            copy_button.press("Enter")
            check("Repeated activation does not duplicate a pending clipboard request", page.evaluate("window.clipboardAttempts") == [commands["net board"]["help_command"]])
            page.evaluate("window.completeCopy()")
            expect(copy_button).to_have_text("Copied")
            check("Successful copy preserves focus and the exact catalog command", copy_button.evaluate("element => element === document.activeElement") and page.evaluate("window.clipboardAttempts") == [commands["net board"]["help_command"]])
            expect(page.locator("#inspector-content .copy-feedback")).to_have_text("Command copied. Nothing executed.")
            page.evaluate("""() => {
                Object.defineProperty(navigator, "clipboard", {configurable: true, value: {
                    writeText: async () => {throw new Error("Permission denied for browser regression");}
                }});
            }""")
            copy_button.press("Enter")
            expect(copy_button).to_have_text("Copy unavailable")
            command_text = page.locator("#inspector-content .command-box code")
            expect(command_text).to_be_focused()
            check("Denied clipboard access selects the exact command for manual copying", page.evaluate("String(window.getSelection())") == commands["net board"]["help_command"])
            expect(page.locator("#inspector-content .copy-feedback")).to_contain_text("Command selected")
            check("Manual copy instructions are visible beside the command", page.locator("#inspector-content .copy-feedback").is_visible())
            page.screenshot(path=str(root / "tooling-copy-fallback.png"), full_page=True)

            page.locator('[data-scope="workflows"]').click()
            page.locator('#tool-list [data-tool-id="calibrated-observable"]').click()
            capability = default["capabilities"][0]
            page.locator("#focus-map").click()
            capability_button = page.locator("#map .capability").filter(has_text=capability).first
            capability_button.focus()
            capability_button.press("Enter")
            expect(page.locator("#search")).to_be_focused()
            check("Keyboard capability filtering reveals and focuses search", page.locator("#search").is_visible() and "map-focused" not in (page.locator(".workspace").get_attribute("class") or ""))
            check("Capability filter text is selected for immediate refinement", page.locator("#search").evaluate("element => element.selectionStart === 0 && element.selectionEnd === element.value.length"))
            check("Clicking a capability searches that declaration", page.locator("#search").input_value() == capability)
            check("Capability navigation narrows to matching workflows", page.locator("#tool-list .tool").count() == len([row for row in catalog["workflows"] if capability.lower() in searchable(row)]))
            page.locator("#clear-search").click()
            dense = max(catalog["workflows"], key=lambda row: len(row["providers"]) + len(row["external_bindings"]))
            page.locator('#tool-list [data-tool-id="' + dense["workflow"] + '"]').click()
            settle_map(page)
            page.locator("#map").evaluate("element => element.scrollTop = element.scrollHeight")
            check("Dense workflow has internally scrollable relationships", map_geometry(page)["scrollTop"] > 0)
            page.set_viewport_size({"width": 1360, "height": 1050})
            settle_map(page)
            check("Resizing a scrolled map preserves connector endpoints", map_geometry(page)["maxEndpointError"] < 3)
            page.locator('#tool-list [data-tool-id="calibrated-observable"]').click()
            settle_map(page)
            check("Selecting another workflow after map scrolling preserves connector endpoints", map_geometry(page)["maxEndpointError"] < 3)
            page.set_viewport_size({"width": 1440, "height": 1050})
            page.locator('#map .map-node[data-requirement-id="tbrt"]').click()
            for theme in ("dark", "light"):
                if page.locator("html").get_attribute("data-theme") != theme:
                    page.locator("#theme-toggle").click()
                for width in (1440, 1024, 736, 390, 320):
                    page.set_viewport_size({"width": width, "height": 1050})
                    page.screenshot(path=str(root / f"tooling-{theme}-width-{width}.png"), full_page=True)
                    check(f"No document overflow in {theme} theme at {width}px including full provider pin", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
            page.close()

            hostile = deepcopy(catalog)
            hostile_text = '</div><script>window.PWNED=true</script><img src="https://invalid.test/pixel"> café λ ⟶'
            next(row for row in hostile["commands"] if row["command"] == "net board")["purpose"] = hostile_text
            hostile_html = root / "tooling-hostile.html"
            with patch.object(operator_catalog, "catalog", return_value=hostile):
                hostile_html.write_bytes(render_html())
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            observe(page)
            page.goto(hostile_html.as_uri())
            page.locator('[data-scope="commands"]').click()
            page.locator("#search").fill("net board")
            page.locator('#tool-list [data-tool-id="net board"]').click()
            expect(page.locator("#inspector-content .description").first).to_have_text(hostile_text)
            check("Hostile Unicode catalog strings render as exact text", True)
            check("Hostile script and image markup stay inert", page.evaluate("window.PWNED === undefined") and page.locator("#inspector-content img").count() == 0)
            page.screenshot(path=str(root / "tooling-hostile.png"), full_page=True)
            page.close()
            browser.close()
        check("Original catalog remains unchanged", catalog == original)
        check("No browser network requests", external == [])
        check("No JavaScript or console errors", errors == [])
    except Exception as error:
        failure = str(error)
    report = {"status": "failed" if failure else "passed", "count": len(checks), "checks": checks,
              "failure": failure, "errors": errors, "external_requests": external,
              "browser_version": browser_version, "offline_document_mode": "file",
              "provider_execution": "not_performed",
              "clipboard_permission_states": "Controlled pending, success and denial; system clipboard untouched", "catalog_counts": {
                  "workflows": len(workflows), "operations": len(operations), "commands": len(commands)}}
    (root / "browser-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
