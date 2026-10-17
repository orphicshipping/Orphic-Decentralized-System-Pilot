# Operational observability: OpenTelemetry and OpenObserve

Decision date: **2026-10-05**. Status: **Planning guide / contract only**.

Adopt OpenTelemetry (OTel) and OTLP as NET's standard operational telemetry
interface, the OTel Collector as its backend-independent collection boundary,
and OpenObserve as the first self-hostable backend target. This is a documented
external-tool selection, not an installed, exercised or production-qualified
integration. No SDK dependency, exporter, Collector deployment, scientific
operation, evidence schema, data family or equipment permission is added here.

## Extend the existing substrate

The [executable architecture](ARCHITECTURE.md) retains ownership of evidence,
operation, execution, result, verification and admission boundaries. The planes
below are responsibility views of that architecture, not new databases,
registries, runtimes or mandatory pipeline stages.

| Plane | Authoritative responsibility | Operational projection may describe |
| --- | --- | --- |
| Evidence | Original observations, source bytes, units, clocks, uncertainty and acquisition provenance | Ingest duration, rejected records, pending work |
| Computation | Declared operation, exact inputs, provider binding, execution and result | Queue wait, execution duration, resource use |
| Verification | Retained check/proof receipt and its precise scope | Verification duration and bounded outcome category |
| Control | Existing policy and separately qualified permission decisions | Decision latency and a reference to the retained decision |
| Operational observability | Sampled/aggregated logs, metrics, traces and optional profiles about operation | Infrastructure diagnosis, not scientific or control authority |

Scientific retention does not wait for the telemetry backend. Applicable evidence
must be retained before downstream computation where its existing acquisition
contract requires it; this document does not change any existing transaction or
write boundary. Failed computation, proof refusal or denied functionality must
not silently erase already retained raw evidence. SP1 remains optional and
profile-specific, not a compulsory stage for every observation or operation.

Existing [scientific telemetry](TELEMETRY.md), including PPDA/STFE/GSIE/SET,
remains unchanged. OTel is not its replacement. Likewise OIT's mathematical
observability/identifiability is not infrastructure observability.

The proposed operational flow is:

```text
Existing device/gateway/NET/provider execution boundaries
    | optional, bounded operational instrumentation
    v
OpenTelemetry API / structured-log bridge
    | OTLP
    v
OpenTelemetry Collector
    | selected exporter and deployment configuration
    v
OpenObserve, or another compatible backend
```

The Collector receives, processes and exports telemetry without requiring a
specific backend [1]. OpenObserve documents OTLP ingestion and self-hosted
single-node/local-disk, object-storage and distributed deployment modes [9,10].
This does not qualify a particular release, edition, authentication model or
factory deployment. Applications must not require OpenObserve-specific storage
or query semantics to execute their scientific work.

## Evidence and diagnostic correlation

A dimension value such as `24.982 mm` belongs to an observation with its original
bytes, instrument identity, units, clock declaration, calibration applicability
and uncertainty state. The naked number is not a complete evidentiary record.
A duration such as `7.2 ms` or a decoder-error count describes infrastructure
operation. Neither an OTel timestamp nor a successful span establishes physical
calibration, measurement validity, a proof receipt or permission to act.

Use standard OTel resource attributes for common service concepts. For new
project-specific attributes use `notations.*`, following the project-namespace
approach in the naming guidance [12]. This distinguishes company concepts from
networking attributes; it does not rename any existing NET evidence identities.
The earlier conceptual `net.evidence.id` examples map to these attribute names.

Illustrative correlation attributes only, not an OTLP wire message or scientific
schema:

```text
service.name = notations-net-gateway
service.instance.id = gateway-process-demo-1
notations.evidence.id = EV-928821
notations.operation.id = ciw.telemetry.v1
notations.execution.id = EXE-demo-1
notations.result.id = RES-demo-1
notations.verification.id = VER-demo-1
notations.device.id = GL-184
notations.gate.id = GATE-demo-1
```

Every reference must be copied from an existing authorized record, never created
to complete a trace. Omit attributes for objects that do not exist or cannot be
resolved. In particular a refused invocation may have an execution record but
no result; no verification reference is invented when a check was not performed.
Use separate bounded outcome attributes for operational status. A successful
span does not mean a specimen, model or verification passed. `operation.id`
names the versioned operation, not one occurrence; `execution.id` identifies
that occurrence. A service process identity is not a physical device identity.

Trace IDs and span IDs are separate OTel identities. They must not replace an
evidence/content identity or enter existing canonical evidence bytes, scientific
result digests, proof inputs or qualification decisions. Diagnostic correlation
belongs in telemetry or a separately authorized diagnostic association, not by
mutating sealed evidence. OTel supports trace/span correlation in logs [3].

Dashplate should resolve references through existing read APIs and authorization.
A link conveys no authority and is not an access token. A missing, sampled-out,
expired or unauthorized trace is an observability limitation, not missing or
invalid scientific evidence. Reopening a workspace must work with no Collector,
backend, trace lookup or SDK installed.

## Traces represent actual execution, not reconstructed physics

Instrument a bounded gateway-to-NET invocation first. Only emit acquisition,
firmware, FPGA or transport spans when those stages are actually instrumented;
never manufacture subspans or timings from a diagram. An MCU or FPGA need not
host an OTel SDK. A gateway may translate declared device diagnostics, retaining
the actual source identity and clock basis rather than claiming it observed the
hardware directly.

One trace is suitable for a bounded causal transaction. Offline batches,
independent proof jobs, fan-in, retries and explicit replay can require multiple
traces linked through span links and retained NET references. OTel supports links
within and across traces [4]. A fresh numerical replay or re-execution follows
the existing rules for new execution/result occurrences; it does not overwrite
the original execution. A transport retry gets a fresh diagnostic attempt but
retains the original measurement/sequence identities and does not itself create
a new numerical execution. Preserve evidence references and supported pins.

Use a local monotonic clock for elapsed durations. Cross-device wall-clock
subtraction is not a valid latency measurement without an established clock
relationship. Keep source event, receipt and knowledge times separate. OTel
context propagation correlates software work, not synchronized measurement
clocks. Do not alter ClockSync, time covariance or temporal admission rules to
make a trace waterfall appear aligned. An unmapped device tick remains unmapped;
its gateway receipt time must not be represented as its acquisition time.

## Sampling, failure isolation and mandatory audit records

The default NET installation and retained-data workflows must remain usable
without an OTel SDK, Collector or OpenObserve. Export is explicitly enabled by
operator configuration. Runtime exporter failures must not escape into the
scientific operation or silently change its outcome. OTel's error-handling
contract prefers losing diagnostics to disrupting the application [5]; NET must
add bounded queues, work budgets and explicit local degradation reporting.

A Collector queue/WAL can improve diagnostic delivery but does not provide
unconditional losslessness. Queue overflow, outages, retry exhaustion and
storage failures can still lose telemetry [6]. Its acknowledgment must never
stand in for NET's scientific retention acknowledgment. Telemetry storage and
CPU/memory budgets must not exhaust evidence-retention resources.

Required security, configuration, calibration-change, release and gate-decision
records remain on their existing durable, unsampled paths. Copies may be emitted
for diagnosis. This is a policy boundary, not a claim that every future audit
record is already implemented. A product requiring a durable audit record may
refuse a sensitive action when that record cannot be retained. That fail-closed
requirement must not depend on a sampled trace or a remote dashboard being up.
Machine interlocks and safety supervision remain independently qualified.

Evidence/execution/result/verification IDs are high-cardinality dimensions: put
them in authorized spans/logs, not ordinary metric labels. Use bounded labels
such as operation family, transport, device model and outcome. Exact device IDs
need an explicit per-site cardinality budget before appearing in metrics. OTel
Views and SDK cardinality limits provide controls [8]. A label is not a reason
to fork the scientific schema. Metric units must be declared; UI conversion from
seconds to milliseconds does not change the recorded instrument unit.

Example metric targets are ingest/execution/proof duration histograms, queue
depth, decode-error and export-drop counters. They are implementation targets,
not delivered NET metrics or an assurance of complete incident capture.

## Customer-owned deployment and security

The initial target is a local/customer-hosted Collector and single-node
OpenObserve. A distributed backend is an optional later capacity/availability
choice, not a dependency of a handheld or a NET desktop. Keep the scientific
store and diagnostic store separately permissioned with independent retention,
backup and resource budgets. The product must remain useful with the backend
replaced or removed.

No telemetry leaves the installation by default. Explicitly allowlist exported
fields and destinations; retain credentials in deployment configuration, not
records, firmware or browser clients. Do not export raw measurement payloads,
formulations, CAD, process recipes, private provider paths, customer identifiers,
exception payloads or credentials by default. Even opaque correlation IDs can
expose production relationships and need tenant-scoped access controls. Backend
queries and Dashplate links require their own authorization checks.

Treat incoming trace context as untrusted metadata, never authentication.
Constrain length/count and avoid unbounded baggage. Do not propagate customer or
secret data in baggage; sanitize/drop context at untrusted boundaries [7]. Shared
factory-LAN access is not authentication. Qualify TLS, scoped credentials, egress
restrictions, retention and backup for the selected backend edition/deployment.
Keep OpenObserve-specific headers, organization/stream routing and endpoint
configuration at the Collector/exporter boundary [9].

Telemetry may later be studied as evidence about a software or physical system,
but only through an explicit existing intake path that retains source bytes,
source identity, clocks, sampling/loss information and the declared analysis.
An alert or metric does not automatically become admitted state, a fault diagnosis
or an equipment command. Preserve an authorized source snapshot; do not present
a transformed query response as the original emission.

## Signal maturity and qualification gates

OTLP specifies stable trace, metric and log signals, but the profiles signal is
still development-status [2]. OpenObserve documents profiles ingestion [11];
that does not establish stable end-to-end compatibility. Keep profiles opt-in,
version-pinned and outside the initial acceptance baseline. API/SDK, Collector
component and backend support require separate version checks.

The first runtime increment must reuse the existing Session/operation/provider
boundaries. Do not create a new execution registry, `net data` family, evidence
store, control route or native logging backend. Do not move existing scientific
providers or gauges into the operational telemetry store. In particular the
separately owned connectivity observation candidate remains a retained scientific
workflow; OTel may observe its execution without replacing its selected-source
records or deriving individual device truth from core aggregate metrics.

| Acceptance case | Required result; none is claimed executed by this document |
| --- | --- |
| Telemetry disabled or SDK absent | Existing imports, execution, save, reopen and replay work without network access |
| Collector/backend unavailable; queue full; exporter fails | For fixed retained inputs, same scientific bytes/results/decisions; bounded work and explicit diagnostic degradation |
| Enabled versus disabled, with fixed retained inputs | Evidence bytes, provider pins, effective parameters, numerical outputs and scientific decisions unchanged; fresh occurrence IDs may differ |
| Success, refusal and malformed request | Correlation reflects actual record creation; no invented result/verification or suppressed refusal |
| Replay, batching and retries | Fresh execution context, preserved source references and truthful span links, no synthetic continuous trace |
| Unknown clock relation | No fabricated event time, transit latency or clock qualification |
| Privacy and untrusted context | No raw/private payload export, cross-tenant lookup or capability grant from attributes/baggage |
| Retention and proof independence | Trace deletion/sampling cannot alter scientific replay or proof inputs; durable required audit records remain separate |
| Cardinality and load | Bounded labels/queues/storage and measured overhead under the declared acquisition/compute budget |
| OTLP backend substitution | Equivalent approved signals accepted by a local test receiver and the selected OpenObserve version; partial rejections handled explicitly |

OTLP can report partial success, so transport-level success is not evidence of
complete ingestion [2]. Record the exact tested SDK/exporter/Collector/backend
versions, signal support and local-versus-hosted results before upgrading the
inventory status. Do not represent a configuration file or a docs check as an
executed end-to-end integration.

## Reconciliation snapshot

Reviewed NET main: `0374076695475669188d61698e4656ced2263e01`.
Reviewed [connectivity draft #202](https://github.com/atomtrapping/Notations-Engineering-Terminal/pull/202):
`1517ef212be6f80092672d1ee6b05ad1df049bfb`, open and unmerged at review.
Its files and the separate parent data-integration work are not edited here.
Existing OTel mentions found in native Cargo lockfiles are dependencies, not
proof of an implemented NET operational-observability interface.

## Primary sources reviewed on 2026-10-05

1. [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/).
2. [OTLP specification and signal stability](https://opentelemetry.io/docs/specs/otlp/).
3. [OpenTelemetry logging and correlation](https://opentelemetry.io/docs/specs/otel/logs/).
4. [Tracing API and span links](https://opentelemetry.io/docs/specs/otel/trace/api/).
5. [OpenTelemetry error handling](https://opentelemetry.io/docs/specs/otel/error-handling/).
6. [Collector resiliency and loss conditions](https://opentelemetry.io/docs/collector/resiliency/).
7. [Context propagation and security](https://opentelemetry.io/docs/concepts/context-propagation/).
8. [Metrics SDK Views and cardinality limits](https://opentelemetry.io/docs/specs/otel/metrics/sdk/).
9. [OpenObserve OTLP ingestion](https://openobserve.ai/docs/ingestion/logs/otlp/).
10. [OpenObserve architecture](https://openobserve.ai/docs/architecture/).
11. [OpenObserve OTLP profiles ingestion](https://openobserve.ai/docs/ingestion/profiles/otlp/).
12. [OpenTelemetry naming conventions](https://opentelemetry.io/docs/specs/semconv/general/naming/).
