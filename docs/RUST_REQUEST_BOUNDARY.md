# Optional Rust request observation

The installed `ciw.request_boundary.RequestBoundary` can observe the existing
`ciw.agent_mcp.Server` prefix and serialized-result byte count. It compares a
bounded Rust result with the Python result. Python still validates, consumes
request IDs, checks current permissions and dispatches operations. Observer
returns are never admission decisions. The default is disabled; no tool, CLI
argument, environment switch or client request enables it.

This is an opt-in host constructor for the existing stdio server. It does not
modify the separate SDK server, add a protocol transport, replace numerical
providers, or establish formal verification. The Rust source and locked build
inputs live in [the experiment directory](../experiments/rust-request-boundary/).

## Build and bind

Use Rust/Cargo 1.91.0 and the committed Cargo.lock. Prepare its exact locked
dependencies from the official registry, then build and test offline:

```sh
cd experiments/rust-request-boundary
cargo +1.91.0 fetch --locked
cargo +1.91.0 test --release --locked --offline
cargo +1.91.0 build --release --locked --offline
```

These commands assume an already installed rustup-managed 1.91.0 toolchain. An
isolated official installation may instead invoke that version's absolute
`cargo` path with explicit `RUSTC`, `RUSTDOC`, `CARGO_HOME` and `CARGO_TARGET_DIR`.
Offline Cargo is not evidence of OS network isolation. Preserve the source,
toolchain, lockfile, crate archive and executable hashes in the release receipt.
The Python wheel does not bundle a platform-specific Rust executable.

A trusted launcher supplies an absolute regular executable and its independently
reviewed SHA-256. Never derive an approved hash from a caller-selected file:

```python
from pathlib import Path
import sys
from ciw.agent_mcp import serve
from ciw.request_boundary import RequestBoundary

observer = RequestBoundary(
    enabled=True,
    binary=Path(approved_absolute_binary),
    binary_sha256=approved_binary_sha256,
    timeout=1.0,
)
serve(existing_principal_facade, sys.stdin.buffer, sys.stdout.buffer,
      request_boundary=observer)
```

`existing_principal_facade` and both approved binary values are host inputs, not
request arguments. Source hashes bind the compared Python contracts, server and
custody implementation. A changed source file refuses observation until a new
review and explicit binding update; no automatic hash refresh occurs.

## Custody and limits

The current executable snapshot requires Linux `memfd_create`, sealing and
`/proc` descriptor execution. Links, nonregular files, missing execute permission,
set-ID bits, oversized files and digest mismatches refuse. After hashing, execution uses an
immutable sealed copy, so replacement of the original path cannot replace the
executed bytes. There is no fallback to execution through an unsealed path.

- The Python server retains its existing 1 MiB request bound, typed IDs,
  duplicate-ID refusal, 4,096-request connection budget and serialized output
  limit. Raw duplicate keys and invalid JSON remain Python refusals.
- The observer's complete contract and executable are each limited to 8 MiB.
  Combined child stdout/stderr is limited to 4,096 bytes; excess output refuses
  without retaining an unbounded buffer. Rust replies never authorize execution.
- The child execution deadline defaults to one second and is limited to two.
  Existing cleanup may additionally wait up to five seconds for the owned child
  and perform bounded reader joins. This is not an end-to-end service SLO;
  trusted source-file checks and filesystem operations are separate work.
- The child receives the explicit `LANG=C.UTF-8` environment and starts in `/`.
  This does not provide network or filesystem isolation. Ordinary descendants
  holding pipes are stopped through the owned process group; a deliberately
  detached descendant is outside that containment claim.
- Ambiguous descriptor close is never retried. Unconfirmed child, reader or
  descriptor cleanup holds this observer against further launches. It does not
  clear an existing adapter, coordinator or transaction hold. The host retains
  the failure object for diagnosis; public observation fields contain no command
  input or exception text.

`last_observation` distinguishes disabled, Python-only raw validation, matched,
mismatched, fallback, held and interrupted states. Ordinary observation failure
leaves Python authoritative. Cancellation remains cancellation even if cleanup
also fails. A canceled prefix observation cannot consume its request ID or
dispatch work through diagnostic fallback. Result observation happens after
request ID consumption and any tool effects; cancellation there does not undo
them. No timeout, reap or canceled observation establishes rollback of provider work.

## Qualification

Run the permanent hook/custody regressions with the qualified Python test
environment, then explicitly select the native executable for differential and
official-client checks:

```sh
PYTHONPATH=src python -m pytest -q tests/test_request_boundary.py tests/test_request_boundary_custody.py
CIW_RUST_BOUNDARY_BINARY=/absolute/approved/odsp-request-boundary \
  PYTHONPATH=src python -m pytest -q experiments/rust-request-boundary/test_boundary.py
```

The native suite requires a real matching observation and an official MCP client
journey through current principal grants. It cannot pass solely by falling back
to Python. Run it again against the installed wheel outside the source checkout,
using the same independently hashed binary. Keep Linux local, installed,
namespace, hosted and formal-proof evidence separate. Missing prerequisites do
not qualify another platform or activate a deployment.
