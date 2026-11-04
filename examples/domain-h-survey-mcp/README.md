# Canonical synthetic survey MCP service

```sh
PYTHONPATH=src python examples/domain-h-survey-mcp/run.py \
  --output /private/new-canonical-survey
```

The launcher binds a self-authored survey to the fixed pipeline, current
principal facade and existing audited wrapper. It exercises catalog, run,
inspection, same-attempt replay and pages through `services.invoke`, retaining
`assembled-analysis.json`, `journey.json`, a redacted audit chain and separate
SQLite CAS head. All results stay held.

Add `--stdio` to connect with an already installed supported MCP client. The
test suite exercises the official SDK and verifies assembled bytes. Standard
output contains protocol messages only. There is no HTTP listener, remote
identity assertion, source selection from requests or equipment connection.

Use a new private directory and private umask. Fresh in-memory test grants apply
only to this synthetic host; saved policy evidence is never current permission.
Real operator binding uses the explicit [service contract](../../docs/SURVEY_PIPELINE_MCP.md)
and separately established source, policy and operational approvals.
