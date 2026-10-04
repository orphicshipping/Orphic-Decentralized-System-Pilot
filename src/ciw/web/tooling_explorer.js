(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const payload = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(byId("tooling-data").textContent.trim()), c => c.charCodeAt(0))));
  if (payload.schema !== "ciw.tooling-explorer.v1" || payload.catalog.schema !== "ciw.operator-catalog.v1") throw new Error("Unsupported tooling catalogue");
  const catalog = payload.catalog;
  const operations = Object.entries(catalog.builtin_providers.operations).map(([id, value]) => ({id, ...value}));
  const state = {scope: "workflows", selected: "calibrated-observable", inspected: null, inspectorTab: "overview", inspectionOrigin: null};
  const list = byId("tool-list"), inspector = byId("inspector-content"), columns = byId("map-columns");
  const workspace = document.querySelector(".workspace");
  const make = (tag, className, text) => {const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el;};
  const readable = value => String(value).replaceAll("_", " ");
  const title = value => readable(value).replaceAll("-", " ").replace(/^./, c => c.toUpperCase());
  const normalized = value => String(value).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const idOf = row => row.workflow || row.id || row.command;
  const rows = () => state.scope === "workflows" ? catalog.workflows : state.scope === "operations" ? operations : catalog.commands;
  const current = () => rows().find(row => idOf(row) === state.selected);
  const requirements = row => [...Object.entries(row.providers || {}).map(([id, value]) => ({id, type: "Pinned provider", value})), ...Object.entries(row.external_bindings || {}).map(([id, value]) => ({id, type: "External binding", value}))];
  const builtinRequirement = row => ({id: row.provider, type: "Built-in manifest", value: catalog.builtin_providers.providers[row.provider]});
  const sourceOf = row => state.scope === "operations" ? catalog.builtin_providers.operations[row.id] : row;
  const matchesRequirement = (node, req) => !!req && node.dataset.requirementId === req.id && node.dataset.requirementType === req.type;
  function button(label, className, action) {const el = make("button", className, label); el.type = "button"; el.addEventListener("click", action); return el;}
  function section(label) {const el = make("section", "detail-section"); el.append(make("h4", null, label)); inspector.append(el); return el;}
  function fact(parent, name, value) {const dl = make("dl", "fact"); dl.append(make("dt", null, name), make("dd", null, value === null || value === undefined ? "Not declared" : typeof value === "object" ? JSON.stringify(value) : String(value))); parent.append(dl);}
  function raw(parent, value, open = false) {const details = make("details", "source-record"); details.open = open; details.append(make("summary", null, "Inspect source declaration"), make("pre", null, JSON.stringify(value, null, 2))); parent.append(details);}
  function command(parent, text, label) {
    const box = make("div", "command-box"), commandText = make("code", null, text);
    commandText.tabIndex = -1; box.append(commandText);
    const feedback = make("p", "copy-feedback"); feedback.hidden = true;
    const original = label || "Copy command";
    let feedbackTimer, copying = false;
    function announce(message) {
      feedback.textContent = message; feedback.hidden = false;
      byId("action-status").textContent = message;
    }
    const copy = button(original, "copy-command", async () => {
      if (copying) return;
      copying = true; clearTimeout(feedbackTimer);
      copy.setAttribute("aria-disabled", "true"); copy.textContent = "Copying…";
      try {
        await navigator.clipboard.writeText(text);
        if (!box.isConnected) return;
        copy.textContent = "Copied"; copy.classList.add("is-copied");
        announce("Command copied. Nothing executed.");
      } catch (_) {
        if (!box.isConnected) return;
        copy.textContent = "Copy unavailable";
        if (document.activeElement === copy) {
          commandText.focus({preventScroll: true});
          const selection = window.getSelection(), range = document.createRange();
          range.selectNodeContents(commandText); selection.removeAllRanges(); selection.addRange(range);
          announce("Clipboard unavailable. Command selected; press Ctrl+C or \u2318C to copy.");
        } else {
          announce("Clipboard unavailable. Select the command text and use your copy shortcut.");
        }
      } finally {
        copying = false; copy.removeAttribute("aria-disabled");
        feedbackTimer = setTimeout(() => {if (copy.isConnected) {copy.textContent = original; copy.classList.remove("is-copied");}}, 1800);
      }
    });
    box.append(copy, feedback); parent.append(box);
  }
  function filterCapability(capability) {
    setMapFocus(false); byId("search").value = capability;
    state.inspected = null; state.inspectionOrigin = null;
    render(); byId("search").focus(); byId("search").select();
  }
  function tags(parent, values) {const group = make("div", "tag-list"); values.forEach(value => group.append(make("span", "tag", value))); parent.append(group);}
  function setMapFocus(enabled) {
    workspace.classList.toggle("map-focused", enabled);
    const control = byId("focus-map");
    if (control) {
      control.setAttribute("aria-pressed", String(enabled));
      control.setAttribute("aria-label", enabled ? "Exit map focus" : "Focus map");
      const label = control.querySelector(".focus-label");
      (label || control).textContent = enabled ? "Exit focus" : "Focus map";
    }
    requestAnimationFrame(drawConnections);
  }
  function inspectRequirement(req, origin) {
    state.inspected = req; state.inspectionOrigin = origin;
    setMapFocus(false); renderInspector(); highlightRequirement();
    if (innerWidth <= 1180) inspector.closest(".inspector").scrollIntoView({block: "start", behavior: "instant"});
    inspector.querySelector(".back-button")?.focus({preventScroll: true});
  }
  function closeRequirement(restoreFocus = true) {
    const req = state.inspected, origin = state.inspectionOrigin;
    state.inspected = null; state.inspectionOrigin = null;
    renderInspector(); highlightRequirement();
    if (restoreFocus) {
      const area = origin === "inspector" ? inspector : columns;
      const target = Array.from(area.querySelectorAll("[data-requirement-id]")).find(node => matchesRequirement(node, req));
      (target || inspector.querySelector(`[data-inspector-tab="${state.inspectorTab}"]`))?.focus({preventScroll: true});
    }
  }
  function renderList() {
    const focusedTool = list.contains(document.activeElement) ? document.activeElement.dataset.toolId : null;
    const query = normalized(byId("search").value).split(" ").filter(Boolean), filter = byId("binding-filter").value;
    const matches = rows().filter(row => {
      const searchable = normalized([idOf(row), row.operation_id, row.role, row.purpose, ...(row.capabilities || []), ...(row.instrument_highlights || []), ...requirements(row).map(req => req.id), row.provider].filter(Boolean).join(" "));
      return query.every(token => searchable.includes(token)) && (state.scope !== "workflows" || filter === "all" || (filter === "builtin" ? row.available === true : row.available === false));
    });
    if (!matches.some(row => idOf(row) === state.selected)) {state.selected = matches.length ? idOf(matches[0]) : null; state.inspected = null;}
    byId("result-count").textContent = `${matches.length} of ${rows().length} tools`;
    list.replaceChildren();
    if (!matches.length) list.append(make("p", "empty-state", "No matching tools. Clear the search or change the session filter."));
    matches.forEach(row => {
      const el = button("", "tool", () => {state.selected = idOf(row); state.inspected = null; state.inspectionOrigin = null; render();});
      el.dataset.toolId = idOf(row); el.setAttribute("aria-pressed", String(idOf(row) === state.selected));
      el.append(make("span", "tool-name", state.scope === "workflows" ? title(row.workflow) : idOf(row)));
      const meta = make("span", "tool-meta");
      if (state.scope === "workflows") meta.append(make("i", "status-dot" + (row.available ? " builtin" : "")), make("span", null, row.available ? "Unbound Session available" : "Explicit bindings required"));
      else meta.textContent = state.scope === "operations" ? (row.capabilities || []).join(" · ") : "Command help";
      el.append(meta); list.append(el);
    });
    if (focusedTool) Array.from(list.querySelectorAll("[data-tool-id]")).find(el => el.dataset.toolId === focusedTool)?.focus({preventScroll: true});
  }
  function mapColumn(label) {const col = make("div", "map-column"); col.append(make("div", "column-label", label)); columns.append(col); return col;}
  function mapNode(parent, label, kind, kicker, action) {
    const el = button("", `map-node ${kind}`, action || (() => closeRequirement(false)));
    if (kicker) el.append(make("span", "node-kicker", kicker)); el.append(make("span", "node-label", label));
    el.dataset.mapLink = String(columns.querySelectorAll(".map-node").length);
    ["mouseenter", "mouseleave", "focus", "blur"].forEach(event => el.addEventListener(event, highlightConnections));
    parent.append(el); return el;
  }
  function requirementNode(parent, req) {
    const el = mapNode(parent, req.id, "provider", req.type, () => inspectRequirement(req, "map"));
    el.dataset.requirementId = req.id; el.dataset.requirementType = req.type;
    if (req.value && req.value.revision) el.append(make("span", "node-meta", req.value.revision.slice(0, 10)));
    return el;
  }
  function highlightConnections() {
    const nodes = Array.from(columns.querySelectorAll(".map-node:not(.selected)"));
    byId("connections").querySelectorAll("path").forEach(path => {
      const node = nodes.find(item => item.dataset.mapLink === path.dataset.mapLink);
      const active = !!node && (matchesRequirement(node, state.inspected) || node.matches(":hover") || node === document.activeElement);
      path.classList.toggle("is-active", active);
    });
  }
  function drawConnections() {
    const svg = byId("connections"); svg.replaceChildren();
    const center = columns.querySelector(".selected"); if (!center || innerWidth <= 800) return;
    const bounds = svg.getBoundingClientRect(), c = center.getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`); svg.setAttribute("preserveAspectRatio", "none");
    columns.querySelectorAll(".map-node:not(.selected)").forEach(node => {
      const r = node.getBoundingClientRect(), left = r.left < c.left;
      const x1 = (left ? r.right : c.right) - bounds.left, x2 = (left ? c.left : r.left) - bounds.left;
      const y1 = (left ? r.top + r.height / 2 : c.top + c.height / 2) - bounds.top;
      const y2 = (left ? c.top + c.height / 2 : r.top + r.height / 2) - bounds.top;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path"), mid = (x1 + x2) / 2;
      path.dataset.mapLink = node.dataset.mapLink;
      if (node.dataset.requirementId) {path.dataset.requirementId = node.dataset.requirementId; path.dataset.requirementType = node.dataset.requirementType;}
      path.setAttribute("d", `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`); svg.append(path);
    });
    highlightConnections();
  }
  function renderMap() {
    const row = current(); columns.replaceChildren();
    if (!row) {
      byId("map-title").textContent = "No matching tools"; byId("map-description").textContent = "Change the filters to explore a declaration.";
      if (byId("map-summary")) byId("map-summary").textContent = "No relationships";
      byId("connections").replaceChildren(); return;
    }
    byId("map-title").textContent = state.scope === "workflows" ? title(row.workflow) : idOf(row);
    byId("map-description").textContent = state.scope === "commands" ? row.purpose : "Connections show catalogue relationships, not execution order or data flow.";
    const reqs = state.scope === "workflows" ? requirements(row) : state.scope === "operations" ? [builtinRequirement(row)] : [];
    if (byId("map-summary")) byId("map-summary").textContent = state.scope === "commands" ? "1 command · 1 help entry" : `${(row.capabilities || []).length} capabilities · ${reqs.length} requirements`;
    const left = mapColumn(state.scope === "commands" ? "Surface" : "Capabilities");
    if (state.scope === "commands") left.append(make("p", "map-note", "NET command plane"));
    else if (!(row.capabilities || []).length) left.append(make("p", "map-note", "No capability tags declared"));
    else row.capabilities.forEach(capability => mapNode(left, capability, "capability", null, () => filterCapability(capability)));
    const middle = mapColumn(state.scope === "workflows" ? "Workflow" : state.scope === "operations" ? "Operation" : "Command");
    mapNode(middle, state.scope === "workflows" ? title(row.workflow) : idOf(row), "selected", row.role ? readable(row.role) : state.scope === "operations" ? "BUILT-IN" : "CLI SURFACE");
    const right = mapColumn(state.scope === "commands" ? "Inspect usage" : "Requirements");
    reqs.forEach(req => requirementNode(right, req));
    if (state.scope === "workflows" && !reqs.length) right.append(make("p", "map-note", "No external provider bindings declared"));
    if (state.scope === "commands") mapNode(right, row.help_command, "provider", "HELP COMMAND", () => {state.inspected = null; state.inspectorTab = "commands"; renderInspector();});
    highlightRequirement(); requestAnimationFrame(drawConnections);
  }
  function highlightRequirement() {
    columns.querySelectorAll("[data-requirement-id]").forEach(node => node.setAttribute("aria-pressed", String(matchesRequirement(node, state.inspected))));
    highlightConnections();
  }
  function inspectorTabs() {
    const nav = make("nav", "inspector-tabs"); nav.setAttribute("aria-label", "Inspector sections");
    ["overview", "bindings", "commands", "source"].forEach(tab => {
      const control = button(title(tab), "inspector-tab", () => {
        state.inspectorTab = tab; renderInspector();
        inspector.querySelector(`[data-inspector-tab="${tab}"]`).focus({preventScroll: true});
      });
      control.dataset.inspectorTab = tab; control.setAttribute("aria-pressed", String(tab === state.inspectorTab)); nav.append(control);
    });
    inspector.append(nav);
  }
  function renderOverview(row) {
    if (state.scope === "workflows") {
      const identity = section("Operation contract"); fact(identity, "Workflow kind", row.source_kind); fact(identity, "Role", row.role); fact(identity, "Scientific CLI route", row.scientific_cli ? "Declared" : "Not declared — use existing Session API");
      const caps = section("Declared capabilities"); if (row.capabilities.length) tags(caps, row.capabilities); else caps.append(make("p", "muted", "No capability tags declared."));
      if (row.instrument_highlights.length) tags(section("Instrument references"), row.instrument_highlights);
    } else if (state.scope === "operations") {
      tags(section("Declared capabilities"), row.capabilities);
      const ports = section("Typed ports");
      ["inputs", "outputs"].forEach(direction => {const entries = Object.entries(row[direction] || {}); if (!entries.length) fact(ports, title(direction), "No ports declared"); entries.forEach(([name, port]) => {fact(ports, `${title(direction)} / ${name}`, port.type ? port.type.schema : port.schema); if (port.type) {fact(ports, "Unit", port.type.unit); fact(ports, "Frame", port.type.frame);} if (port.path) fact(ports, "Result path", port.path);});});
    } else {
      inspector.append(make("p", "description", row.purpose));
      inspector.append(make("p", "description", "This view indexes the command surface. Its arguments, provider bindings and qualification remain specific to the command."));
    }
    const scope = section("Evidence boundary"); fact(scope, "Catalogue qualification", catalog.qualification); fact(scope, "Execution authority", "Not granted by catalogue");
  }
  function renderBindings(row) {
    const reqs = state.scope === "workflows" ? requirements(row) : state.scope === "operations" ? [builtinRequirement(row)] : [];
    const bindings = section(`Provider requirements · ${reqs.length}`);
    reqs.forEach(req => {
      const control = button(req.id, "requirement", () => inspectRequirement(req, "inspector"));
      control.dataset.requirementId = req.id; control.dataset.requirementType = req.type;
      control.append(make("span", null, req.type + (req.value && req.value.revision ? " · " + req.value.revision.slice(0, 12) : ""))); bindings.append(control);
    });
    if (!reqs.length) bindings.append(make("p", "muted", state.scope === "commands" ? "Provider requirements belong to each command's workflows. Inspect its help for usage." : "No external bindings declared for this workflow."));
    if (state.scope === "operations") {const runtime = section("Runtime identity"); Object.entries(row.runtime).forEach(([key, value]) => fact(runtime, title(key), value));}
    bindings.append(make("p", "description", "Declarations only. Local presence and runtime conformance have not been checked."));
  }
  function renderCommands(row) {
    const next = section(state.scope === "operations" ? "Inspect through NET" : "Continue in your terminal");
    if (state.scope === "workflows") {
      if (requirements(row).length || row.requires_upstream_bundle) command(next, row.setup_command, "Copy setup plan command");
      command(next, row.scientific_cli ? row.execution_entry : "ciw serve --help", "Copy usage command");
      if (!row.scientific_cli) next.append(make("p", "description", "Use the existing Session operation.execute API for this workflow."));
      next.append(make("p", "description", "Commands are copied only. Replace ROOT with your explicit monorepo path where needed."));
    } else if (state.scope === "operations") command(next, "net capabilities " + row.capabilities[0] + " --json");
    else command(next, row.help_command, "Copy help command");
  }
  function renderInspector() {
    inspector.replaceChildren(); inspector.scrollTop = 0; const row = current();
    byId("selection-kind").textContent = state.inspected ? state.inspected.type : state.scope === "workflows" ? "Workflow" : state.scope === "operations" ? "Operation" : "Command";
    if (!row) {inspector.append(make("p", "empty-state", "Select a tool to inspect its declaration.")); return;}
    if (state.inspected) {
      const req = state.inspected;
      inspector.append(button("← Back to tool", "back-button", () => closeRequirement()), make("h3", null, req.id), make("p", "identity", req.type));
      inspector.append(make("p", "description", "Requirement declared by " + idOf(row) + ". Local presence and runtime conformance have not been checked."));
      const details = section("Exact declaration");
      if (req.value && typeof req.value === "object") Object.entries(req.value).forEach(([key, value]) => fact(details, readable(key), value));
      else fact(details, "Requirement", req.value);
      raw(inspector, req.value); return;
    }
    inspector.append(make("h3", null, state.scope === "workflows" ? title(row.workflow) : idOf(row)));
    if (state.scope === "workflows") {
      inspector.append(make("p", "identity", row.operation_id));
      inspector.append(make("p", "status-label" + (row.available ? " builtin" : ""), row.available ? "Available in an unbound Session" : "Explicit provider bindings required"));
    } else if (state.scope === "operations") {
      inspector.append(make("p", "identity", row.provider));
      inspector.append(make("p", "status-label", row.bound ? "Declared binding present" : "Provider not bound by catalogue"));
    }
    inspectorTabs();
    if (state.inspectorTab === "overview") renderOverview(row);
    else if (state.inspectorTab === "bindings") renderBindings(row);
    else if (state.inspectorTab === "commands") renderCommands(row);
    else raw(inspector, sourceOf(row), true);
  }
  function render() {renderList(); renderMap(); renderInspector();}
  function clearFilters() {
    byId("search").value = ""; byId("binding-filter").value = "all";
    state.inspected = null; state.inspectionOrigin = null; setMapFocus(false); render(); byId("search").focus();
  }
  byId("workflow-count").textContent = catalog.workflows.length; byId("operation-count").textContent = operations.length; byId("command-count").textContent = catalog.commands.length;
  byId("inventory").textContent = `${catalog.workflows.length} workflow declarations\n${catalog.commands.length} command surfaces`;
  byId("search").addEventListener("input", () => {state.inspected = null; state.inspectionOrigin = null; render();});
  byId("binding-filter").addEventListener("change", () => {state.inspected = null; state.inspectionOrigin = null; render();});
  byId("clear-search").addEventListener("click", clearFilters);
  document.querySelectorAll("[data-scope]").forEach(control => control.addEventListener("click", () => {
    state.scope = control.dataset.scope; state.selected = null; state.inspected = null; state.inspectionOrigin = null; state.inspectorTab = "overview";
    byId("search").value = ""; byId("binding-filter").value = "all";
    document.querySelectorAll("[data-scope]").forEach(tab => tab.setAttribute("aria-pressed", String(tab === control)));
    byId("binding-filter").hidden = state.scope !== "workflows"; document.querySelector(".filter-label").hidden = state.scope !== "workflows"; render();
  }));
  const theme = byId("theme-toggle");
  function setTheme(light) {
    document.documentElement.dataset.theme = light ? "light" : "dark";
    if (theme) {
      theme.setAttribute("aria-pressed", String(light)); theme.setAttribute("aria-label", light ? "Switch to dark theme" : "Switch to light theme");
      const label = theme.querySelector(".theme-label"); (label || theme).textContent = light ? "Dark theme" : "Light theme";
    }
  }
  setTheme(false);
  theme?.addEventListener("click", () => setTheme(document.documentElement.dataset.theme !== "light"));
  byId("focus-map")?.addEventListener("click", () => setMapFocus(!workspace.classList.contains("map-focused")));
  document.addEventListener("keydown", event => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.isComposing) return;
    const editing = event.target instanceof Element && (event.target.matches("input, textarea, select") || event.target.isContentEditable);
    if (event.key === "/" && !editing) {event.preventDefault(); setMapFocus(false); byId("search").focus();}
    if (event.key === "Escape") {
      if (state.inspected) {event.preventDefault(); closeRequirement();}
      else if (byId("search").value || byId("binding-filter").value !== "all") {event.preventDefault(); clearFilters();}
      else if (workspace.classList.contains("map-focused")) {event.preventDefault(); setMapFocus(false); byId("focus-map")?.focus();}
    }
  });
  new ResizeObserver(drawConnections).observe(byId("map"));
  render();
})();
