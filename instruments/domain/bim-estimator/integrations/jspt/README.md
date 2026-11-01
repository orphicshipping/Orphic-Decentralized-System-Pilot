<!-- ncp-folder-entry -->
# MCP entry

- path: `instruments/domain/bim-estimator/integrations/jspt`
- class: `unbound`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# JSPT consumption

GAT calls `sensitivity.push_covariance`. It does not form `T @ P @ T.T`.

Pin a JSPT SHA in CI. Tests skip if `sensitivity` is not installed.

```
pip install -e ../Jacobian-Sensitivity-Propagation-Testbed
pytest tests/test_jspt_consume.py
```
