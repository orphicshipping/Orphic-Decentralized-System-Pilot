# GaugeLink MCP refit: local verification, 7 October 2026

The first delivery of the approved architecture sequence is implemented and locally verified on Windows. This is protocol, retention and arithmetic verification over synthetic evidence; it is not physical qualification.

## Results

| Run | Result |
| --- | --- |
| New GaugeLink domain/CLI/MCP tests plus operator discovery | 16 passed |
| Existing Board host/CLI/recovery and scalar improvement regressions | 92 passed |
| Existing visual Board, Needle, System Board, parameter and semantic regressions | 100 passed |
| Installed-wheel GaugeLink and Board host/CLI/official-SDK/recovery tests | 50 passed, no failures or skips |
| CI event-policy check | Passed for 19 workflows and 14 required release triggers |
| Staged whitespace validation | Passed |

The 208 source checks and 50 installed checks overlap in coverage and must not be presented as 258 independent scientific reference cases. The first source XML run emitted only a pytest report-format warning; the installed report used the compatible format. No Linux CI run or remote-client qualification is claimed.

The wheel was built offline without installing dependencies into the shared Python environment, then installed into a separate target directory. Installed tests disabled the source-tree import override and launched the package from that target.

Wheel SHA-256: `30f264194e51b315e1e109ddf5ed857ed210425d1673cbb8368b4c0223dd8852`.

## Retained demonstration

The installed SDK completed nine tool calls across three separate stdio server sessions using negotiated protocol `2025-11-25`. Reconnection and exact retry retained the same candidate. Separate tests also retrieve that MCP-created candidate through CLI.

Four original scalar occurrences survive unchanged. The baseline adds a retained basis, scenario and audit; the candidate reuses that basis and adds only a scenario and audit. Provider-call counting checks that no original scalar operation is re-executed. Existing pure payload validators still run when retained data is loaded.

The synthetic example changes the hypothetical offset error amplitude from 0.5 to 0.25. Scenario variance changes from `0.00103308` to `0.00076433`, with standard uncertainty changing from approximately `0.032141561879` to `0.027646518768` in the original mean unit. The fixed objective is 0.03. Independent arithmetic audit passes, while acceptance remains false and review status remains unreviewed. No acquisition or physical improvement was established.

Candidate: `sha256:6fd893927d1356c0f73ac5ad2a80d491a54c55997caefa61ab04d7d95743ca37`.

Local generated artifacts are deliberately outside versioned source:

- `work/gaugelink-mcp-demo/RESULT.md`
- `work/gaugelink-mcp-demo/journey-receipt.json`
- `work/gaugelink-mcp-demo/profile/mcp-config.json`
- `work/link-refit-source.xml`, `work/link-refit-inherited.xml`, `work/link-refit-installed.xml`
- `work/link-refit-wheel/computational_instrumentation_workbench-0.1.0-py3-none-any.whl`

The user-facing source contract is [GaugeLink MCP refit](GAUGELINK_MCP_REFIT.md). Later PDF, certificate, reporting and domain-adapter work remains tracked in [the implementation sequence](NCP_GAUGELINK_IMPLEMENTATION.md). Client activation and remote publication have not occurred.
