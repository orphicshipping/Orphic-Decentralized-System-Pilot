# Private host acceptance — 2026-10-17

Status: **not accepted yet**. Live execution succeeded, but a later artifact-read
transaction fenced the host. Recovery and same-host restart acceptance remain
blocked pending explicit approval. No numerical execution has been retried.

## Deployed provenance

- Numerical baseline: `c47a8621cd97f8d46afe526ed15534111fc6e347` (PR #240).
- Installed wheel SHA-256: `ebddbc4d1db02437dd083ffb968068ed096521d0f065ca1a70fba130fd7dcce3`.
- Container image: `registry.cloudflare.com/962a6859d0119b4348a295679641e9b1/ncp-control@sha256:5a28412ac59bc8fbbdbd360251b54a9d9901688902101bab3e60259f5a5eb679`.
- Private panel: <https://ncp-live-workbench.notationsystems.chatgpt.site>.
- Site source: `a72d4bfd13f71c69843ff2f73bc4f372f2110506`.
- Site deployment: `appgdep_6ad3eb5975c48191ae00d0e89d377a8f`, succeeded;
  secret environment revision 1. Owner-only audience preserved.
- Artifact archive SHA-256: `63dcaf502589379bbd0e61e1befc21eb65bcb2ae247b7026d78ec22437512a15`.

## Verified results

- Backend and private Site anonymous API requests returned HTTP 401.
- Published Site authenticated `/api/status` returned HTTP 200 with genuine MCP
  protocol `2026-07-28`, 14 tools, and a committed retained-state transaction.
- Live discovery returned nine numerical capabilities and seven explicit grants.
- Live MCP plan/create/admit/run completed run
  `run-b268a7eb6ae74296ab3974b2800b9eee` with seven execution records.
- Private R2 checkpoint `checkpoints/61ca6850-09df-444d-af0d-b83bbb6d63e7.tar.gz.base64`
  was saved at 21:43:26 UTC. Administrative readback independently found the
  completed run and all seven numerical outputs, each `declared_model_only`.
- Retained graph artifact bytes hash exactly to
  `6145c2b115d9cd6f736154cceea03629e2b9467bcdc51d249db927077f2b7584`, matching its
  recorded identity. The checkpoint's base64-file hash is
  `12a4dd4f88ce9ed9864a2bbd652b999d8f1195a2b8c085c20c06c914c9e4656e` (including newline).
- Cloudflare reported the container inactive at 21:45:25 UTC. Reading the same
  run through the restarted hosted API has **not** passed yet.

## Failure and remaining gate

The acceptance runner failed during `ncp_artifact_read`, after the completed run
was acknowledged. The Worker returned HTTP 503 `outcome_unknown` for transaction
`4cbd9869-3334-4ec4-b505-4aece6abc17a`; its durable fence blocks subsequent calls.
The original failure did not retain sufficient diagnostic detail to distinguish
container transport, adapter failure, or checkpoint publication failure. Do not
label it a numerical-kernel failure or claim the cause is established.

A proposed new authenticated checkpoint-export route was rejected by safety
review. It was never deployed and was removed from source. The checkpoint was
instead read through the existing administrative Cloudflare R2 API. A one-time
administrative recovery, preserving all checkpoints and replaying no numerical
work, has been requested. Do not clear the fence blindly.

## Local verification and review

- Four adapter tests pass against the installed real MCP child: discovery,
  exact-JSON plan creation, known refusal followed by successful admission,
  seven-operation execution and artifact reads, retained run recovery, duplicate
  attempt identity, oversized checkpoint rejection, and uncertain-response fence.
  The deliberate invalid-response test substitutes only the bridge boundary.
- Three gateway tests pass; five existing operator-console tests pass.
- Two panel DOM tests and artifact/proxy boundary validation pass. Clean npm
  install/build/test also passes from the GitHub source copy.
- Separate requirements and documented-standards reviews found three defects:
  null refusal polling, inconsistent checkpoint bounds, and an un-fenced invalid
  server response. All were reproduced, fixed, regression-tested, and re-reviewed.
  Duplicate read-tool classification remains a nonblocking maintenance note.
- These are not full-repository, physical-metrology, provider-qualification, or
  visual-browser acceptance claims. No browser-control facility was available.

The four-attempt synthetic profile has used one successful attempt. No new native
Streamable HTTP MCP endpoint, unrestricted data import, or browser restart control
is claimed. Keep the PR draft until the remaining recovery gate is verified.
