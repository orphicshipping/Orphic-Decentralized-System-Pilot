# Host-selected native principal policy

`RustPrincipalPolicy` is an opt-in subclass of the existing `PrincipalPolicy`.
Its Rust process validates the current policy and decides principal, operation,
scientific-operation, typed constraint and resource access. `snapshot`,
`check_principal`, `authorize`, `authorize_resource`, `context` and
`authorize_context` have no Python authorization fallback. A failed process,
changed binding or invalid reply refuses the operation.

This ports principal-policy decisions. The principal facade, authenticated
transport, retained-artifact ownership, current execution lifecycle, handoff,
receipt admission, and session/coordinator state machines remain Python. The
shared `services.admit` request-shape gate also remains Python and executes
independently after a native policy decision. It is not a native authorization
fallback. Direct native-policy tests disable the Python decision routines; the
combined facade test separately verifies that this shared service validator is
still called only at the service boundary. The separate Rust numerical backend retains its own supported-operation and
qualification contract. This change does not establish a complete Rust control
engine, native session engine, SP1 proof, Kani proof, or hosted qualification.

## Host integration

Only trusted launcher code selects the executable, its independently qualified
SHA-256, a policy reader and process limits. There is no request argument, tool,
checkpoint field, environment switch or automatic fallback that selects it.
Existing launch defaults are unchanged.

```python
from ciw.process_policy import ProcessPolicy
from ciw.rust_principal_policy import RustPrincipalPolicy

limits = ProcessPolicy(
    environment={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    wall_seconds=3,
    cpu_seconds=3,
    memory_bytes=256 * 1024 * 1024,
    file_bytes=1024 * 1024,
    max_input_bytes=2 * 1024 * 1024,
    max_output_bytes=1024 * 1024,
    expected_prlimit_sha256=qualified_prlimit_sha256,
)
policy = RustPrincipalPolicy.from_file(
    host_policy_path,
    binary=qualified_native_executable,
    expected_sha256=qualified_native_sha256,
    process_policy=limits,
)
facade = policy.facade(authenticated_principal, control=bound_control_host)
```

The variables above come from the existing trusted host configuration. A caller
must not supply any of them. `from_file` uses the existing bounded, duplicate-key
rejecting, no-follow policy reader. A custom zero-argument reader must return a
coherent current snapshot; the class lock cannot serialize an unrelated external
writer. `authorize_context` reads once under the same lock and obtains both the
authorization decision and its context from that one native snapshot. Later
facade checks still apply current revocation and scope rules.

The wrapper uses Python only for host binding, exact transport types, byte/work
bounds, current-policy acquisition, process management and reply validation.
It does not call Python `validate_policy`, `_request`, `_rule`, `_direct_science`
or any overridden Python authorization method to reach an acceptance decision.

## Source and protocol binding

The build embeds SHA-256 values for its Rust sources, manifests and lockfile,
the existing execution-commitment implementation, and the Python sources that
define the policy semantics, schemas and direct scientific-operation mapping.
The runtime catalog is derived from those host modules, never from a request.
It contains the existing tool schemas and operation mappings without aliases.

Construction requires a release binary built against the current Python source
hashes. Each process call rechecks those source hashes, the actual current
registered catalog and executable bytes. It runs a private copy of the verified
executable and checks the source/catalog binding again before returning its
result. `runtime_identity()` also checks the current source, catalog and binary
binding before returning identity. A registry or source update requires an
intentional rebuild and fresh qualification.

The closed `odsp.rust-policy.v1` envelope carries a catalog digest and a complete
current policy. Responses bind to the exact request digest and result bytes.
All policy and scientific identity hashes retain the existing Python canonical
JSON representation. Numeric tokens preserve integer versus float, booleans,
signed zero and exact comparisons at integer/float boundaries. Constraints use
the existing typed canonical-byte equality; schema enums use their existing
typed equality semantics.

Plain JSON cannot distinguish an explicit Python surrogate pair from an astral
Unicode scalar. The required inner text encoding `odsp.surrogate-escape.v1`
therefore maps each literal tilde to `~~` and each literal surrogate code point
to `~uXXXX;` with four lowercase hexadecimal digits. It applies to values and
object keys before JSON encoding, and is reversed before native validation and
policy hashing. Normal Unicode scalars remain unchanged. Replies use the same
encoding. Marker-looking user text is escaped, malformed markers refuse, and
literal surrogate pairs cannot become valid request strings during transport.

The native parser rejects duplicate decoded keys, trailing input, invalid
numbers, unknown envelope fields and unsupported schema constructs. The current
registered schema vocabulary is implemented in Rust; a new unsupported schema
refuses catalog qualification instead of weakening validation.

## Process and failure contract

The native wire is at most 2 MiB input and 1 MiB output. Policies retain the
262,144-byte limit and requests retain their 65,536-byte envelope limit, with
their existing depth, Unicode, integer and tree budgets. The host may reduce
process limits. Native-policy selection caps CPU/wall time at ten seconds,
address space at 1 GiB and file writes at 1 MiB. Inherited environment variables
are removed; only explicitly supplied PATH and locale variables are accepted.

The existing pinned `prlimit` process mechanism enforces the selected limits,
bounded pipes and process-group cleanup. Uncertain cleanup holds that policy
instance against further launches. The default `limits` mode is not filesystem
or network isolation, nor an aggregate cgroup limit. Bubblewrap mode still
requires the existing successful namespace probe; this policy port does not
claim that such isolation was qualified. Neither mode protects against a local
administrator changing trusted process memory or the host configuration.

## Reproduction and qualification

Build with the pinned Rust 1.91.0 toolchain and the committed lockfile. Obtain
missing locked crates through the official registry before using offline mode.
Keep target output outside a source checkout when preserving a release tree.

```sh
cargo test --locked --offline --manifest-path native/odsp-control-core/Cargo.toml
cargo build --release --locked --offline --manifest-path native/odsp-control-core/Cargo.toml
```

Set `ODSP_NATIVE_POLICY_BINARY` to the absolute release executable. The test
fixture independently checks every embedded source hash against the checkout.
An absent executable is a visible skip, not successful native qualification.
`ODSP_NATIVE_POLICY_EVIDENCE` optionally selects an external directory for the
actual differential-case record.

```sh
PYTHONPATH=src:tests python -m pytest tests/test_rust_principal_policy.py tests/test_rust_principal_policy_review.py -q
```

The differential suite compares the real subprocess against actual Python
policy methods over every registered schema, current policy mutations, direct
and nested scientific operations, typed constraints, Unicode, resources and
seeded malformed requests. Positive authorization cases are required. The
independent review includes in-memory registry drift during a child process,
binary drift, reply replay, malformed wire, exact Unicode round trips and sticky
cleanup uncertainty.

The following harness selects the native subclass only in the test process and
its real MCP stdio child. It disables original Python authorization methods and
validators, then runs existing facade, MCP, HTTP and signed workflow tests:

```sh
PYTHONPATH=src:tests python native/odsp-control-core/qualify_existing.py \
  --binary /absolute/path/to/odsp-control-core \
  --expected-sha256 QUALIFIED_BINARY_SHA256 \
  --report /absolute/evidence/native-existing.json -- \
  tests/test_principal_policy.py tests/test_principal_http.py \
  tests/test_mcp_toolbox.py tests/test_toolbox_skills.py \
  -k 'not closed_host_policy' -q
```

The omitted `closed_host_policy` cases directly test the deliberately disabled
Python validator; the native suite separately exercises closed-policy
validation. Local HTTP/SDK tests use local fixture issuers and identities. They
do not establish a production OAuth issuer, hosted storage, deployment safety,
live physical qualification or a full scientific-baseline result.
