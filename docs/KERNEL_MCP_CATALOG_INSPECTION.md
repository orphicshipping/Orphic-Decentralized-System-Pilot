# Native catalog integrity and retained MCP inspection

The kernel on this path is the existing CIW contract and composition layer:
Session owns execution/retention, CapabilityRegistry owns declarations and
explicit trusted-code binding, native content identities own encoding, and
agent_mcp owns the existing stdio transport. This increment adds no runtime,
scientific ledger, provider loader, retrieval index or numerical engine.

## Native interfaces

`ciw.control_plane.registry_from_catalog(value)` is the existing saved-catalog
reader factored out of the CLI. It validates provider ownership, operation IDs,
runtime identity, ports and selectors with native validators, and returns an
unbound CapabilityRegistry. The CLI `net capabilities --catalog ...` uses it.
Runtime comparisons use the existing v1 content identity: booleans, integers,
floats and signed zero retain their native distinctions. The same comparison
guards live binding both before execution and after the provider returns.

`ciw.control_plane.inspect_provider_catalog(value)` returns
`ciw.provider-catalog-inspection.v1`. Its `record` preserves saved declarations,
including historical `bound` claims; `catalog` contains the validated unbound
projection. `binding_status=declarations_only`, `authorizes_execution=false`
and `verification_status=not_performed` are explicit. Inspection neither probes
runtime callbacks nor binds or executes a provider.

Both `ciw.net.inspect_path(path)` and the existing MCP `net_inspect` use that
native inspection function. MCP inputs remain operator-selected, frozen bytes.
Responses retain the original `binding.artifact_id`, source-byte `sha256`,
`schema` and `size_bytes`. These source-byte references are distinct from
canonical content identities and execution/result/verification occurrences.
Selectors operate on the native inspection projection. Whole responses above
the existing 256 KiB budget refuse; clients can select a smaller field.

## Reproducible fixture

`examples/agents/provider-catalog/catalog.json` is an authored contract fixture,
not installed-chain evidence. Its historical binding flags are deliberately true
and its instruction-like note is inert untrusted data. The profile grants no
execution and has no output directory. Launch the existing entrypoint:

```sh
python -m ciw.agent_mcp serve --profile /absolute/path/examples/agents/provider-catalog/profile.json
```

After MCP initialization, call:

```json
{"name":"net_inspect","arguments":{"artifact":"catalog","selector":["catalog","operations","statistics.v1"]}}
```

The selected operation has `bound=false`. To inspect the historical claim,
select `["record","operations","statistics.v1","bound"]`; its true value remains
a saved declaration, never authorization. `net_capabilities` describes this
host's own registry and grants separately.

The test-only official Python client stays at the existing `mcp==1.26.0` pin:

```sh
python scripts/check_provider_catalog_mcp.py --fixture examples/agents/provider-catalog --output-dir /new/local/check-directory
```

The check launches `python -I -m ciw.agent_mcp serve`, exercises SDK discovery,
bounded reads, frozen source replacement, domain refusals, both declared
protocol versions, notification silence, line-size refusal and EOF shutdown.
The raw transport observer checks every stdout line. Add `--workspace` with an
authorized retained built-in Session workspace to verify its original execution
and result records through the same read-only process. The check does not
generate a new scientific execution.

## NCP and GaugeLink handoff

NCP may supply exact native workspace bytes using:

```python
AgentHost(registry=builtin_registry(), inputs={"workspace": workspace_bytes},
          output_dir=None, allow_operations=())
```

Then `net_inspect({"artifact":"workspace"})` returns the existing Session
reader's execution/result projection and the original source-byte binding.
The current workspace schema does not retain the original Session ID; NCP must
keep that ID in its original production manifest. Scratch reader IDs are not
the original Session identity. Trusted owner code must register any
operation-specific payload validators before reopening records that need them.
Saved text cannot select imports or register validators. PR219 scalar workflow
integration and its specific validators require separate combined testing.

GaugeLink only needs the existing producer contract: exact versioned operation
and provider ownership, runtime identity, declared port schemas/units/frames,
explicit selectors, and original retained evidence and occurrence references.
A saved catalog does not bind code. Explicit live binding does not grant
execution, and execution or validation does not establish physical correctness.
No unit/frame conversion is inferred; use an explicitly registered transformation.

No corpus source was ingested. Staged sources, inspector pages and provenance
graphs do not establish extraction, embeddings, retrieval or AI-use rights.
Unresolved rights and coverage remain unresolved.

## Protocol scope

The baseline remains `2025-11-25` and `2025-06-18`, implemented by the existing
local server without a runtime MCP SDK dependency. SDK usage is test-only.
This is parent-owned stdio, not HTTP or browser WebMCP. A completed-request
cancellation notification is harmless; the sequential server does not promise
in-flight provider cancellation. Named ChatGPT/Grok/Codex host activation is
separate from a successful local SDK process check.

Official references checked 2026-10-07:
[stdio framing](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[tool results and errors](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
[historical lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle),
[Python SDK 1.26.0](https://pypi.org/project/mcp/1.26.0/).
