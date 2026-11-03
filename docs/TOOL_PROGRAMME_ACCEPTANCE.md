# Coordinated tool programme: acceptance contract

This campaign extends merged `cef7d007` locally. The previously reviewed frontend
source `b2ff583f` and its container remain separate frozen deliverables. This
document does not publish, merge, deploy, create credentials, grant a capability,
change a source pin or clear an uncertain transaction.

One integration owner owns services, principal policy and generated inventories.
Specialist workers own disjoint source/test files; an independent reviewer probes
the combined boundaries. Worker passes are evidence about their slices, not a
combined release result. The qualification receipt must name the exact final
source/tree, interpreter, installed wheel and commands.

| Capability | Implemented contract | Acceptance and remaining gate |
| --- | --- | --- |
| Strict Python boundaries | Bounded raw JSON, closed schemas, full request/receipt bindings | Seeded property/mutation and forged-content tests; existing canonical identities unchanged |
| Locked SBOM | Actual wheel/archive hashes, every owned package root and service closure | Generate twice from final release; official CycloneDX schema validation separately required |
| Process execution | Host-pinned launcher, explicit environment and Linux resource limits; optional Bubblewrap | Actual timeout/flood/cancellation tests; positive namespace isolation requires a permitted runtime |
| Audit | Bounded redacted hash chain, independent anchor, durable unresolved-call fence | Real process/restart/fault tests plus explicit host reconciliation; no automatic unknown retry |
| Queue | Atomic owner/principal/generation-bound claims with kernel lease and monotonic fence | Concurrent/crash/context-exit tests and existing coordinator dispatch; downstream systems must enforce their own fence |
| Proof verification | Host key registry and full Ed25519-attributed receipt checks | Real signature/forgery/revocation tests; no signature-to-computation-proof promotion |
| Native SP1 | Reuse existing registered verifier behind bounded host configuration | Held until registered host/guest binaries and genuine proof fixtures are supplied and qualified |
| ROS / OPC UA / MQTT | Closed read-only offline observation exports | Actual SDK/MCP, full receipt comparison and current grants; no wire decoder, broker, device or control access |
| Metrology / GIS / finance / trade | Read exact host-bound saved contracts/observations | Existing validators, complete receipt verification, zero execution/retention tests; no live feed/trade/measurement claim |
| Knowledge | Existing ODRP reference profile | Preserve separate corpus/evidence grants; no new corpus ingestion or authority |
| NSR / SRA / ClockSync / NISE | Existing pinned computations with optional explicit process policy | Real native input/replay tests; preserve numerical semantics and all imported trees |
| Strict client / viewer | Read-only typed canonical client and evidence view | Static compiler, adversarial response and browser gates; no UI-derived authority |
| Toolbox / skills / isolated cache | Client selection over current host schemas/capture/trust bindings; bounded versioned recipes through canonical/audited authority; separate immutable-read cache | Combined source and installed qualification must bind the final commit; pending/unknown results stop dependencies, current grants and fixed receipt ownership remain mandatory |
| Rust request boundary | Installed opt-in observer, sealed Linux executable snapshot and exact Python source bindings | Actual native comparisons and official-client grant checks; cancellation/cleanup faults must preserve diagnostics and prevent unresolved observers from relaunching; Python stays authoritative |

**Containment is a separate gate.** Linux process limits are not a network or
filesystem sandbox and process-group cleanup cannot guarantee termination of a
descendant that deliberately detaches. A requested strict namespace profile must
refuse if its backend is unavailable; limits mode is not an automatic substitute.
Per-process memory limits are not aggregate descendant limits. Do not run an
untrusted binary merely because these local tests passed.

**Custody is a separate gate.** Known-history restart and live-writer exclusion do
not establish lost-history disaster recovery. Restoring a copied queue/audit
directory cannot authorize old writers or reconstruct a missing independent
anchor. Existing backup/fence tests and deployment holds remain necessary.

**Identity is a separate gate.** Local principal bindings, pinned source and
synthetic captures are not deployed authentication. Host-only paths and declared
roles remain host inputs, while remote Access/credential/storage binding needs
its own approved deployment verification. The existing private hostname and
paused portal are not replaced by this campaign.

The executable [qualification runner](../scripts/qualify_tool_programme.py)
records bounded sequential phases. Missing native inputs, namespace support,
installed evidence or external approvals are explicit held/partial results.
Timeouts and test skips cannot become successful qualification. No operational
SLO, RPO or RTO is committed by a local test deadline.
