# Live observation intake

`ciw.live_intake.LiveIntakeHost` adds three principal-bound MCP operations:
`ncp_live_sources`, `ncp_live_read` and `ncp_live_receipt`. A trusted launcher
binds each source's protocol, endpoint, exact selector, SDK version, credential
epoch and process limits. Tool arguments select an existing opaque source ID,
an observation count (1–32) and a window (0.05–10 seconds). They cannot select an
endpoint, executable, credential, tenant or principal.

The implemented clients are Paho MQTT 2.1.0, asyncua 2.1.0 and a version-bound
ROS2 `rclpy` JointState subscriber. MQTT and OPC UA have been exercised against
real local middleware. The ROS2 adapter is source implementation only until a
qualified ROS2 installation is available; the official package host was denied
by the execution environment. Installing the Python package does not connect
any source or grant permission to use a device.

## Host binding and authorization

Create `LiveBinding` objects and a `LiveIntakeHost` in trusted launcher code.
The host requires an explicit Python executable digest, `ProcessPolicy` in
limits mode, approved import roots and a fresh authorization callback. The
callback receives the operation and detached arguments and returns a bounded
nonempty current authority epoch or refuses. Credential resolution is a separate
host callback; returned values go to the child on bounded stdin and are absent
from receipts and redacted errors. Epochs must change when the corresponding
credential or source authorization changes.

Bind this host through `PrincipalPolicy.facade(..., live=host)`. The facade
binds its actual policy instance to the live host. Every source read then checks
both current source authority and canonical principal grants using
`authorize_context`, which authorizes against one coherent policy snapshot.
Retained reads also require the original source-read grant and exact original
selectors. A receipt-read grant alone cannot disclose a revoked source.

The `AuditedPrincipalHost` composition installs an additional request-bound
source guard before dispatch and rechecks it after durable audit I/O. Revocation
after an observation or receipt read withholds the result and records uncertainty;
it does not erase a completed audit event, undo the read or authorize a retry.
An observed uncertain process cleanup permanently holds that host instance.

## Connection contracts

- MQTT uses one exact topic without wildcards. Live bindings require TLS and
  certificate verification. Success requires a successful CONNACK and a matching
  successful SUBACK for the requested topic/QoS. An elapsed observation window
  can produce an empty or partial receipt only after that handshake. Original
  payload bytes, topic, QoS, retained/duplicate flags and local acquisition time
  are retained. The client never publishes a measurement or control message.
- OPC UA reads one fixed node with an exact expected native datatype and unit.
  Live bindings require Basic256Sha256 SignAndEncrypt, a fixed server certificate
  and host-provided client credentials. Native quality/status, source/server
  timestamps, value and acquisition time are retained. Only expiration of the
  caller's observation window after a successful connection permits partials;
  connection, security and SDK request failures refuse. No node writes or method
  calls are implemented.
- ROS2 subscribes to one fixed JointState topic, DDS domain, joint set/types and
  clock declaration. A live binding requires an existing security keystore with
  `ROS_SECURITY_ENABLE=true` and `ROS_SECURITY_STRATEGY=Enforce`. Local fixture
  bindings require localhost discovery. No ROS graph-wide discovery, motion,
  parameter services or actuation is exposed by the adapter.

`local_fixture` TCP endpoints must be literal loopback addresses. A fixture label
does not qualify a physical instrument. `live` bindings require separate approval
for their named endpoints and credentials; none were created by this campaign.

## Execution and records

The parent uses the existing bounded process runner with a fresh working
directory, wall/CPU/address-space/output limits and process-group cleanup.
This is a resource limit boundary, not a network or filesystem sandbox.
The child starts with `-I -S`, no site startup and a fresh bytecode prefix. The
launcher snapshots all bytes in its explicit SDK import roots, the worker and
its package initializer, Python executable and public certificate files.
Those bindings are checked again after credential I/O and after execution.
The initial snapshot is host custody, not proof that an arbitrary installation
is trustworthy; system libraries and the full Python standard library are not
claimed to be hermetic.

Each successful connection read creates a new occurrence with exact source,
principal, tenant, request, runtime binding and normalized offline interpretation.
Receipts use the existing bounded retention store with eviction disabled. A
receipt page must match the checked detached receipt's complete canonical page
envelope before disclosure. Results explicitly retain `authorizes_execution=false`,
`physical_validity_established=false` and unestablished source-clock synchronization.
Connection authentication does not establish traceable calibration or timing.

## Reproduction and open gates

`tools/live-intake/runtime-lock.json` records official Debian package inputs and
the 42 exact Python wheel hashes. `requirements.lock` is installed with hashes
from an empty environment by `bootstrap.py`; no substitute cached dependency
version is accepted. The bootstrap creates local inputs only and starts no
service. The tests use those inputs to run local Mosquitto and asyncua servers,
including TLS/SignAndEncrypt fixtures and adversarial shutdown/authorization cases.
Test credentials are temporary local fixtures, not deployment credentials.

Run `tests/test_live_intake.py` and `tests/test_live_intake_review.py` with the
explicit runtime paths described by their fixtures. Qualification receipts must
record the current source hashes, runtime hashes, exact command and all skips.
ROS2 middleware execution, named physical devices, production credential custody,
network reachability, source clock synchronization and calibration remain separate
gates. No physical or hosted qualification is inferred from the local tests.
