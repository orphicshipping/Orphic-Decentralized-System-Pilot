# Combined installed acceptance

This local candidate combines the existing PR219 engineering workflow and retained-source inspection with the Kernel owner's native identity and catalog reader patch. It consumes GaugeLink's retained synthetic captures through existing native contracts. Acquisition stays with GaugeLink; corrections and arithmetic stay with the existing scalar operations.

The integration helper `scripts/check_ncp_integrated.py` must run under an isolated, noneditable installation with `python -I`. It accepts an explicit producer directory, SHA256 of its `summary.json`, an existing golden `journey.json` and its SHA256, and a fresh output directory. It records each real CLI command and each MCP request/response. Paths and hashes are operator-selected inputs, not evidence admission.

```text
<installed-python> -I scripts/check_ncp_integrated.py --producer-root <retained-gaugelink-directory> --summary-ref sha256:<summary-hash> --golden-journey <prior-ncp-journey.json> --golden-ref sha256:<journey-hash> --output-dir <new-directory>
```

This executes only the three supported synthetic acceptance cases. Source inspection and compilation occur first; missing compute grants and stale compilation references must refuse before output creation. The explicit `--allow-compute` path invokes the existing operations. Expected means are 0.03125 N for valid data and 1.0 N for the insufficient-context fixture; interrupted input retains a refusal and no result. Missing covariance, variance and standard uncertainty remain null. The insufficient-context case retains arithmetic PASS and nominal FAIL. All three retain physical qualification HELD.

The same candidate then reopens the saved workspaces, checks original bytes and occurrence IDs, compares `ncp` and `net` output bytes, and inspects both new and prior workspaces through the existing `AgentHost`/stdio server for the two supported MCP revisions. Prior golden records use their external thread reference and thread manifest; no generated ID is normalized away. Object comparisons preserve JSON type and signed-zero distinctions. MCP must report an exact read-only refusal, no output capability, no grants and zero dispatches after the server exits. Catalog inspection consumes the Kernel owner's native reader.

Historical inspection recognizes two exact v1 compiler fingerprints whose gate predicates and recipe were reviewed as unchanged: PR219 plus retained-source inspection, and that same compiler with the Kernel owner's control-plane patch. Original compiler, runtime and gate identities remain retained. This compatibility allowlist permits reading only; it does not establish current compilation verification, authenticity or authority. Unknown fingerprints refuse. Executing either old compilation under the changed compiler requires a separately prepared and explicitly authorized new compilation. The production reader continues to check all retained acceptance outcomes using those reviewed predicates.

The installed checker anchors both producer request and capture envelopes to the prior golden references, rejects output beneath retained inputs, runs workflow modules with Python isolation, and sanitizes Python environment variables for the two console-alias checks. It also restores each original golden thread into a new directory and compares every retained byte and original occurrence ID before read-only MCP inspection; restoration creates no execution authority.

Keep the independent audit/export regression in the installed gate:

```text
<installed-python> -I -m pytest -q tests/test_link_scientific_guards.py::test_failed_arithmetic_audit_blocks_export_before_output_creation -o pythonpath= -o addopts=
```

That test injects a bounded arithmetic fault into the established scientific module while leaving the independent audit intact. Four completed operations must retain audit FAIL and refuse export before creating the destination. It does not claim physical validation.

The combined package is a local integration candidate, not a merged branch, published release, configured ChatGPT/Codex/Grok host or instrument qualification. The current main documentation update and separate Workbench/typed-data development are not incorporated. Exact candidate hashes, installed paths, command logs, scope and unresolved coverage are recorded in this task's evidence-bearing handoff.

## Integrated Board and GaugeLink candidate (7 October 2026)

The isolated integration joins the `18e5b0c` NCP consumer line with the existing
`003f3c2` Board/covariance-refit line, whose common ancestor is `d242abd` (#224).
The `control`, `refit`, and `gaugelink-refit` commands coexist. The `mcp-control`
and `board-mcp` extras both retain SDK 2.3.0. Existing NET/NCP aliases remain.
The new combined wheel is paired with the separately identified GaugeLink
`b09b6d2` producer wheel; the three scientific profiles retain separate grants.

`scripts/check_control_mcp.py --instrument link-scalar --request REQUEST` verifies
retained scalar decoding, correction, shared-covariance mean and independent audit
under both supported protocols. `scripts/check_board_refit_mcp.py --output-dir NEW`
verifies the original oscillator Board journey independently under MCP 2025-11-25
and 2026-07-28, each across three server processes, with exact retry and CLI status
inspection. Run both using the same installed `python -I`. The Board checker uses
the retained source archive's acceptance test helper; its hash joins the receipt.
Neither checker transfers grants or authorizes candidate acceptance.

Installed generated refit client configurations use `-I` so a caller's working
directory or PYTHONPATH cannot replace installed modules. Source development keeps
its explicit source-root configuration for compatibility. The installed Board
checker deliberately supplies a shadow package through both ambient paths and
requires the installed server to remain authoritative.

The corpus coverage command builds separate frozen metadata-only topic catalogs
and one ID-complete coverage index. Historical intake verification, fresh byte
verification, full-text admission, rights and execution authority remain separate.
See [CORPUS_COVERAGE.md](CORPUS_COVERAGE.md). Original snapshot pins remain valid.

The previous broad diagnostic ended with a native Windows runtime fault and no
final XML; it is not a passing candidate qualification. Subsequent qualification
uses process-level deadlines on the identified Windows runtime, without its
problematic timed faulthandler watchdog. Exact platform results, artifact hashes,
unresolved diagnostic failures and release holds belong in the private combined
handoff, not in aggregate test-count claims. No merge, publication, live client
activation, physical acquisition or physical qualification is implied.
