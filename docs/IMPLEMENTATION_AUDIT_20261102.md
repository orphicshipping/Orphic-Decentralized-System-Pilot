# Orphic Systems implementation audit — 2026-11-02

## Scope and conclusion

Reviewed base commit `26a437ef1ec664069a948ab9b3f546ac7c3e1e35` of
`orphicsystems/Orphic-Decentralized-Systems-Protocol`.
This is a scoped source and local execution audit of the shared service/MCP
boundary, plan admission and recovery, retained artifacts, pinned reference
disclosure, packaging, branding, and isolated private-host gateway/fence logic.
It is not an exhaustive audit of all scientific solvers or vendored projects.

The existing service substrate is implemented and supports bounded synthetic
execution and pinned reference disclosure. Discovery, execution grants, retained
evidence, and scientific qualification remain distinct. Keep the verification
status `INCOMPLETE`; these results do not establish production readiness,
physical qualification, native runtime availability, or decentralized replication.

## Findings

| ID | Severity | Evidence and impact | Disposition |
| --- | --- | --- | --- |
| A01 | Required before Windows retention qualification | `src/ciw/retention.py`, `Store._read`, uses `os.O_NOFOLLOW` unconditionally although `SUPPORTED_PLATFORMS` includes `nt`. Removing that POSIX-only attribute in a controlled test produces `AttributeError` when reading an otherwise valid retained record. The exception is not the intended refusal. This is a portability defect, not a demonstrated authorization bypass. | Open. Implement a Windows no-follow/reparse-point-safe reader and test it on Windows, or explicitly refuse the unsupported platform. Do not silently drop symlink protection. |
| A02 | Medium | `src/ciw/services.py`, `invoke`, directly returns host-free operation results, whereas `ControlHost.call` checks encoded output against `MAX_RESPONSE`. Substituting a controlled 300,000-character result for `ncp_plug_in` was accepted by the SDK. This demonstrates a boundary difference; it does not demonstrate that an unmodified solver currently emits that result. | Open. Define whether SDK output shares the MCP response bound; enforce a shared postcondition or document/test the intentional difference. Preserve retained failure locators when enforcing it. |
| A03 | Medium | The installed-package regression test hard-coded `/usr/bin/python3.11` and `/tmp`, preventing execution on this Python 3.12 host and Windows. | Fixed in this change: use `sys.executable` and pytest's temporary directory; retain the outside-checkout installation and invocation checks. |
| A04 | Medium | README and plug-in product fields retained the former product name; package repository/docs/issue links targeted the former repository; several generated UI surfaces displayed the former company name. | Fixed in this change. Active display text and links identify Orphic Systems / ODSP. Packaged and checkout plug-in copies agree. |
| A05 | Release evidence gap | The five latest workflow records returned during this audit had `startup_failure`, at other commit SHAs. They are not results for this change or proof of the cause. | Open. Obtain successful hosted checks for the exact proposed commit before release; diagnose startup failures separately. Example record: https://github.com/orphicsystems/Orphic-Decentralized-Systems-Protocol/actions/runs/41393319285. |

## Protected behavior

The exercised tests cover plan creation/admission, host-authority refusal,
same-attempt retry identity and conflict rejection, retained recovery without
re-execution, interrupted intent becoming unknown, owner-scoped retention,
catalog/evidence pin checks, and reference disclosure without external-work grants.
The Node checks cover unauthenticated/foreign-origin refusal, bounded routing,
historical-generation refusal and durable uncertainty fences. They emulate the
Worker boundary; they do not qualify Cloudflare storage or container execution.

Branding preserves `ncp_*`, `net_*`, `ncp://`, schema IDs, `ciw` imports,
distribution identity, commands, existing server names and deployment bindings.
The legacy stdio server adds the human-readable ODSP `title`. Copyright notices,
license text, third-party attribution and retained historical evidence remain.
ODRP remains the existing read-only reference profile of ODSP.

## Validation and limits

The shell could not authenticate a Git clone. Files were retrieved through the
authorized GitHub connection at the fixed commit. The repository tree contains
7,645 entries; 1,672 retrieved files initially matched their Git blob hashes.
The local extraction includes package sources, tests and selected fixtures; it
is not a complete checkout of every vendored file. The local Git index was
populated from the returned snapshot for inventory reconciliation. Ledger row
reconciliation therefore checks that snapshot, not a fresh Git clone.

Local environment: Linux, Python 3.12, pytest 9.0.2, numpy 2.4.3, MCP 2.3.0.
Dependencies were installed for the audit; repository dependency pins were not
changed. Initial failures caused by missing extracted fixtures/dependencies were
resolved before the final affected-suite run. Overlapping initial wheel builds
collided in the shared build directory; final packaging validation runs serially.

Final executed checks:

- `python -m pytest tests/test_control_gateway.py tests/test_reference_profile.py tests/test_transformation_ledger.py tests/test_knowledge_mcp.py tests/test_proto_corpus.py tests/test_evidence_corpus.py -q --tb=short`: **172 passed** (25.00 s), including installed-package invocation outside the checkout.
- `python -m pytest tests/test_mcp_project_map.py -k "domain_set or workbench" -q --tb=short`: **2 passed, 2 deselected**. The two full-tree inventory checks were not claimed.
- `node --test tools/odrp-private-host-candidate/tools/private-mcp-host/test_gateway.mjs tools/odrp-private-host-candidate/tools/private-mcp-host/test_worker.mjs`: **7 passed**.
- Manual stdio initialization reports the ODSP title while preserving `net-agent`; `ciw.services.invoke("ncp_plug_in")` returns the Orphic product name. Packaged and checkout plug-in copies match.
- `PYTHONPATH=src python scripts/write_transformation_ledger.py`: regenerated 6,843 file rows after adding this report, document/capability indexes and packaged copies.

 Full folder
contract verification, whole-tree scientific execution, Windows execution,
container builds, production corpus access, cloud deployment and physical
qualification are outside the evidence obtained in this pass.

## Follow-up order

1. Resolve A01 with native Windows tests while preserving no-follow protection.
2. Resolve A02's SDK/MCP result contract and test both routes with bounded and
   oversized results, including retained refusal records.
3. Run complete folder/index checks and affected suites in a full checkout.
4. Obtain hosted CI for the exact candidate commit, then qualify separately
   provisioned engines and an isolated private deployment with retained receipts.
