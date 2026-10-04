# Tooling map and capability inspector

Export the installed NET operator catalog as a standalone HTML file:

```sh
net visualize --output tooling.html
```

Open `tooling.html` in a browser. The explorer provides a searchable tooling map
and an inspector for existing workflows, declared provider roles, capabilities,
command surfaces and binding requirements. Its content comes from the same
`operator_catalog.catalog()` used by `net catalog --json`; the export preserves
that catalog unchanged inside a `ciw.tooling-explorer.v1` envelope.

Search by workflow, capability or provider role. Spaces, hyphens and underscores
are interchangeable for search, and every word must match: `calibrated observable`
finds `calibrated-observable`. Click a capability in the map to use it as a filter,
or a provider requirement to inspect its full declared revision and identity.
Capability selection reveals and focuses the search field so you can refine the
filter immediately with the keyboard, including when leaving map focus.
Connections show catalog relationships, not execution order or data flow. The
active connection follows provider inspection, pointer hover and keyboard focus.

The inspector separates each tool into four views:

- **Overview** shows identity, declared capabilities, availability and typed ports
  where those ports are declared.
- **Bindings** lists provider and external binding requirements; select one to
  inspect its exact declaration.
- **Commands** provides existing NET help and setup-plan commands to copy into a
  terminal.
- **Source** displays the selected tool's unchanged catalog declaration.

Use the theme button for light or dark appearance and the map focus button for
more room to inspect relationships. Inspecting a provider returns to the full
layout so its details are visible. Press `/` to focus search when not editing a
field; this also leaves map focus so the search is visible. Press `Escape` to
return from a provider, clear an active search or filter, or exit map focus. The selection and inspector controls also work with
ordinary Tab and Enter keyboard navigation. Copy actions keep keyboard focus.
If browser clipboard access is unavailable, the command is selected for manual
copying and instructions appear beside it; press Ctrl+C or Command+C.

The page works offline with embedded assets and a Content Security Policy that
disables network connections. Exporting and inspecting it do not provision,
bind, execute or qualify providers. Availability and binding labels describe
the catalog's unbound Session declarations, not checks of this machine or a live
Session. Commands shown in the inspector are text for use separately in a
terminal.

Each export is a snapshot. Generate a new file after installing a newer NET
version; an existing output path is never replaced. Export failures return exit
code 2 and a JSON refusal on stderr.

Existing Board and result viewers remain separate tools:

- [System Board views and offline editing](SYSTEM_BOARD_VISUAL.md)
- [Mathematical model exploration](MODEL_EXPLORATION.md)
- [Provider-free capability index](CAPABILITIES.md)

## Browser regression checks

With the optional `playwright` Python package and its Chromium browser installed,
run from the repository with NET available on the Python path:

```sh
python validation/tooling_visual_browser.py --output-dir NEW_VALIDATION_DIRECTORY
```

Use `--chromium /path/to/chromium-or-edge` for an existing browser executable.
The script navigates generated `file:` pages and saves HTML, screenshots and
`browser-report.json` into a new directory. It checks catalog parity, provider
pins, all four inspector views, exact Source records, keyboard focus and
shortcuts, normalized workflow search and filters, capability navigation,
matching active connectors, map focus, command help and typed ports. Clipboard
checks cover exact command text, pending requests, focus preservation and the
manual-copy fallback when access is denied. Map checks
verify the default provider cards remain visible and connectors stay aligned
after internal scrolling, resizing and selection changes. It also checks
hostile strings as inert text and both themes at five widths from 320 to
1440 pixels. It fails on browser errors, network requests or document overflow.
These checks
qualify the explorer UI only; they do not qualify scientific providers.
