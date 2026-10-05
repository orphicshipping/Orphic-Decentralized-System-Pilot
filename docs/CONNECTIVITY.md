# Handheld and private-core connectivity observations

**Delivered scope:** an executable, read-only AMF aggregate-observation workflow
through NET's existing Session, OperationRegistry, source identities, execution
records and offline reopen/replay. Open5GS and free5GC remain external network
infrastructure. This is not a new telecom core, default hardware provider,
network manager, subscriber store, or measurement-evidence store.

The immediate path is:

`authorized metrics export -> selected aggregate evidence -> Session operation -> retained record -> local report`

A handheld or edge process can supply a request to this same workflow. The
increment does not install Android software, implement a modem driver, connect
a phone to a private network, deploy the public Dashplate, or automatically
register a family in the draft `net data` integration.

## Run a complete synthetic example

After installing the normal NET package:

```sh
net connectivity catalog
net connectivity example --provider open5gs --output open5gs-request.json
net connectivity run open5gs-request.json --output-dir open5gs-investigation
net connectivity inspect open5gs-investigation
net connectivity replay open5gs-investigation --output-dir open5gs-replay
net connectivity report open5gs-investigation --output open5gs-report.html
```

Use `--provider free5gc` for the other authored example. All destinations must
be new. The report is a standalone, responsive, monochrome instrument panel
with no scripts or external requests. It explicitly identifies retained versus
live state, synthetic provenance, missing metrics and lack of verification.
Opening it is not equipment-control authorization.

`run` performs the real `connectivity.observe.v1` operation. Inspection reopens
the native workspace and checks its source/result relationships without
invoking the registered runtime or making network requests. Replay executes a
new local projection with fresh execution/result occurrence identities while
preserving original request bytes and evidence identity. These are software
consistency checks, not native-core or physical verification.

## Import an operator-owned export

```sh
net connectivity context --provider open5gs --output context-template.json
# Review the template and save an accurate operator context as context.json.
net connectivity import context.json metrics.prom \
  --observed-at 2026-10-05T16:00:00-04:00 --clock-id collector.clock.1 \
  --output captured-request.json
net connectivity run captured-request.json --output-dir captured-investigation
```

The context generator is **synthetic**, not network discovery. Replace its
instance/network aliases, declared configuration content reference, PLMN,
data-network name and slice with accurate non-secret declarations before an
operator import. `provider_revision` is the actual component revision when
known, otherwise explicit `null`; the template's reviewed source revision is
not proof of the deployed executable. `metrics_namespace` is explicit because
free5GC constructs these names from a configurable namespace.

Keep raw exports private and outside version control. Only use information you
are authorized to collect. This importer does not access an operator network.

## Explicit one-shot local capture

For an already-running, authorized local exporter, the CLI can make one GET:

```sh
net connectivity capture context.json \
  --endpoint http://127.0.0.5:9090/metrics --timeout-s 3 \
  --output captured-request.json
```

That address is an example, not an automatically discovered endpoint. The
operator must verify it belongs to the intended exporter. NET never starts a
core, opens an SSH tunnel, alters a firewall, follows a redirect, enumerates a
subnet, or supplies authentication credentials. Only HTTP `/metrics` on an
explicit loopback IP and port 1024..65535 is admitted. IPv4 loopback and IPv6
`::1` are accepted; hostnames, IPv4-mapped IPv6, user information, queries,
fragments, other paths and non-loopback addresses are refused before connection.
HTTP proxy environment variables are not used. An existing output refuses
before GET. Responses are bounded plain-text exports; compression, ambiguous
framing and non-200 status codes refuse.

The capture has socket timeouts and a body-read deadline, not a real-time or
adversarial slow-header service guarantee. Run only against trusted local
exporters. Refusals return nonzero; the CLI does not manufacture successful
observations. Saved requests, Session parameters, inspection and replay can
never select an endpoint or invoke capture.

## Reviewed provider profiles, not a universal metric mapping

| Provider/component | Selected gauge | Preserved semantics |
| --- | --- | --- |
| Open5GS AMF | `ran_ue` | Native RAN-UE aggregate |
| Open5GS AMF | `amf_session` | Native AMF-session aggregate |
| Open5GS AMF | `gnb` | Native gNodeB aggregate |
| free5GC AMF | `<namespace>_amf_business_ue_connectivity` | Native UE-connectivity aggregate with `access_type` labels |

For an empty free5GC namespace, the leading namespace and underscore are
omitted. Supported access labels are exactly `3GPP_ACCESS` and
`NON_3GPP_ACCESS`. They are separate samples, not silently summed. Open5GS
and free5GC counters are **not** treated as scientifically equivalent metrics.
Missing expected gauges remain missing, including an exporter with no selected
metrics; they never become zero or a claim that a device is disconnected.

Reviewed source identities:

* Open5GS `open5gs/open5gs`, commit
  `5877b43196fca0b185b266f6616979c86c962a4c`,
  [`src/amf/metrics.c`](https://github.com/open5gs/open5gs/blob/5877b43196fca0b185b266f6616979c86c962a4c/src/amf/metrics.c),
  blob `804b73af62370ee5823cb3500874dd3720cee244`.
* free5GC **component** `free5gc/amf`, commit
  `602c7ea4921a9e5094b1635e862abf5566b6b3f2`,
  [`internal/metrics/business/types.go`](https://github.com/free5gc/amf/blob/602c7ea4921a9e5094b1635e862abf5566b6b3f2/internal/metrics/business/types.go),
  blob `393fae5f0a846207005bdef3c58e143325920ecc`, and
  [`ue.go`](https://github.com/free5gc/amf/blob/602c7ea4921a9e5094b1635e862abf5566b6b3f2/internal/metrics/business/ue.go),
  blob `bb2dbb8ffa46099414b9103d370e72962e52b652`.

The free5GC profile follows that component source, not a claim that every
free5GC superproject release pins or enables those metrics. Configurations
must enable the applicable exporter/metric group separately. The external
programs were not executed by this source review. No upstream code is vendored
and upstream licenses are unchanged. First-party additions follow NET's
existing `AGPL-3.0-or-later` grant and component notices.

## Evidence and timing contract

The context keeps provider, AMF instance, network, PLMN digit strings, DNN,
optional S-NSSAI and declared configuration reference separate. These are
operator declarations, not source authentication. No SIM keys, subscriber
records, raw IMSIs, SUPIs, SUCIs, IMEIs or phone numbers belong in the request.
Use locally assigned aliases for equipment and network-session references.
Unknown fields and non-allowlisted sample labels refuse. Arbitrary text aliases
are not a general privacy scanner: the operator remains responsible for their
contents.

The projection retains **exact selected lines**, including their line endings
and original line numbers, plus SHA-256/length/line-count of the whole input.
Unselected samples, HELP text and comments are deliberately omitted rather
than copied into public or general-purpose evidence stores. This means the
retained bundle alone cannot recompute the whole-export hash. It can check
its selected projection and source/result binding. Preserve the private
original export separately when full-source audit is required; a hash is not
proof of authenticity or permission to publish the source.

One retained transport link may associate a content reference, device alias,
network-session alias and edge alias with separate sample/receive timestamps
and clock identities. The referenced observation is **not loaded, resolved or
authenticated** in V1. Timestamps are exact integer nanoseconds. Same declared
clock yields the signed arithmetic difference; different clocks produce
`CLOCK_RELATION_UNKNOWN` and no duration. A negative same-clock difference
stays `INCONSISTENT_DECLARED_ORDER`, not clipped to zero. No clock relation,
uncertainty, time synchronization, one-way UE latency or sensor fusion is
inferred. HTTP elapsed time is collector request duration, not UE packet delay.

Bounds: export 1 MiB / 16,384 lines; each inspected line at most 1,024
characters; 32 selected lines; request 256 KiB; 64 transport links; native
workspace 4 MiB. This is a narrow nonnegative-integer gauge parser with
preceding TYPE declarations, not general Prometheus/OpenMetrics, histograms,
exemplars, native timestamps or a time-series database.

## Existing architecture and integration handoff

`operator_commands.py` adds one `net connectivity` delegation and
`scientific_audit.py` adds its explicit review reference. Existing numerical
kernels, covariance representation, generic schemas, Session, operation runner,
Workbench and other command semantics are unchanged. The local runtime is
explicitly registered only for this workflow. Its payload validator permits
provider-free reopening after trusted registration; saved data cannot register
code. Current UI selection may change without rewriting historical operation
selection or results.

PR #185's typed data/field/thermal bridges are not changed. Its integration
owner may later add a route for these versioned native records, retaining the
original request bytes, selected-line policy, source/evidence identities,
clock declarations and separate occurrence IDs. A new data route must not
conflate declared network-session aliases with NET Session IDs or promote
aggregate availability into authenticated per-device connectivity.

## Verification and remaining gates

```sh
python -m pytest -q tests/test_connectivity.py
python -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist
python scripts/check_connectivity_installation.py \
  --wheel dist/computational_instrumentation_workbench-0.1.0-py3-none-any.whl \
  --output-dir /absolute/new/qualification-directory --require-pins
```

The installed gate creates an isolated noneditable environment, explicitly
copies its named dependency distributions from the operator's host with a
file-hash receipt, removes source-path variables, compares all package bytes
against the source and wheel, copies tests outside the checkout and exercises
both providers' installed native CLI journeys. `--require-pins` refuses a host
that differs from the unchanged declared dependency versions. Omitting it
permits an explicitly recorded alternate-host experiment, not qualification of
the pins. CI requires the pins on Linux/Windows and Python 3.11/3.13.

Tests use **authored synthetic exports**, including real local HTTP transport;
they do not run Open5GS/free5GC. Local qualification recovered the complete
`src/ciw` package tree matching main
`0374076695475669188d61698e4656ced2263e01`, not the complete imported monorepo.
The recovery method and exact executed results are reported in the PR; a CI
workflow definition is not evidence of a hosted pass.

Outstanding separate targets: native-core exporter conformance, deployed
configuration/executable identity, LTE MME metrics, controlled software UE/RAN
integration, real handheld modem/OS interfaces, independently checked clock
mapping, authenticated per-device/session evidence and field validation.
Subscriber provisioning, slicing changes, packet capture, actuation and RF
transmission are not implemented. The software grants no radio or spectrum
authorization. A real radio deployment requires separately reviewed equipment,
security and applicable regulatory permissions; a PLMN identifier is not such
permission.
