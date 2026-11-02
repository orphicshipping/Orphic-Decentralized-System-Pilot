# Orphic Decentralized Reference Protocol (ODRP)

ODRP is the reference profile of the existing ODSP substrate. It reuses
`KnowledgeHost`, `ProtoCorpus` and `EvidenceCorpus`. ODSP execution and ODRP
reference disclosure remain separate authority boundaries. The common service
entrypoint is `ciw.services.invoke`, not a second retrieval implementation.

NSL captured-state and OLM retained-logistics reads use separate host bindings;
see [read-only adapter profiles](READ_ONLY_ADAPTERS.md). Their discovery entries
do not add grants or tools to this ODRP host.

## Host-owned bindings and shared software API

```python
from pathlib import Path
from ciw.services import open_reference, reference_tools, invoke
host = open_reference(catalog_path=Path(catalog), catalog_ref=catalog_ref,
                      evidence_path=Path(evidence), evidence_ref=evidence_ref)
tools = reference_tools(host)
result = invoke("ncp_evidence_search", {"query": "calibration"}, host=host)
```

Paths and pins are selected by the trusted local launcher. They are never tool
arguments. `reference_tools` returns the typed descriptions actually available
on this host. Without a bound evidence bundle, no evidence tools are advertised.
Existing `ncp_knowledge_*`, `ncp_evidence_*`, `ncp_system_status` and `ncp://`
resource identities remain compatible. The software service returns the `data`
portion of the same successful response envelope used by MCP, consistent with
existing service behavior; refusals raise `ValueError` with a fixed reason.

## Command-line and MCP

Existing `ncp knowledge` and `ncp evidence` commands are retained for operator
compilation. This change adds `ncp reference status|tools|serve`. For example,
using exact values from the trusted compiler receipts:

```sh
ncp reference serve --catalog /absolute/catalog --catalog-ref sha256:EXACT_PIN \
  --evidence /absolute/evidence --evidence-ref sha256:EXACT_PIN
```

These are placeholders, not valid pins. `python -m ciw.net reference ...` is
equivalent. The MCP server is named `odrp-reference`. No cloud service is started.

## Scope and safety

All clients connected to one local process can read its selected disclosure
snapshot. This is not per-document multi-tenant ACL enforcement or remote
identity management. Do not expose it directly to a network. External-work
capabilities are not composed into ReferenceHost. Discovery grants no execution
permission, and all passage content remains untrusted data.

Metadata record hashes, source hashes, representation hashes and passage hashes
remain distinct. Existing declared relationships can be read; no cross-snapshot
supersession editing, inferred latest version, or new document-level grants are
introduced here. Immutable bundle relocation is portable; authenticated node
synchronization and a decentralized deployment remain future qualification.

## Verification and outstanding gates

The local integration test covers actual upstream compilation, the common
services route, typed discovery, exact passage/provenance, refusals, snapshot
restart/relocation and a real MCP client/server subprocess outside the checkout.
The local environment exercised both MCP 1.29.0's compatibility adapter and
ODSP's pinned MCP 2.3.0 path, which delegates to the existing adapter. SDK 2.3.0
and pytest were installed only into isolated temporary environments; no global
dependency pins were changed. A wheel built from all 767 Git-blob-verified
package-source/metadata files plus this patch was installed in a fresh Python
3.12 environment with numpy 2.4.3 and MCP 2.3.0. From an external working
directory, 165 catalog/evidence/knowledge-host/CLI/retention/integration tests
passed using that installed wheel, including the real MCP subprocess and SDK
journey. This is complete package-source extraction, not a full repository
checkout or hosted CI. See the accompanying wheel receipt for exact hashes.

Before merging/releasing: run full affected knowledge/evidence/service/CLI suites
in the complete target checkout,
verify regenerated transformation/document/capability indexes against the final checkout,
and review the patch against the latest branch. This change does not qualify
unrelated native engines, move the real corpus, deploy Cloudflare, or alter
identity/access policy.

## Operating runbook

1. **Prepare:** use a complete approved ODSP installation, record its exact Git
   commit/wheel hash, Python/SDK versions and this integration patch digest.
   Keep the current deployment intact until candidate acceptance succeeds.
2. **Bind:** trusted operator selects catalog/evidence paths and exact compiler
   receipt pins. Check `ncp reference status` and `ncp reference tools` with the
   same flags intended for `serve`. No tool may choose paths, credentials or pins.
3. **Launch:** `ncp reference serve` uses stdio; configure a local MCP client to
   start that command with the selected arguments. Do not expose a TCP listener
   or treat this as remote authentication. Software uses the same `open_reference`
   host and `invoke` operations.
4. **Health:** enumerate tools/resources and read `ncp_system_status`; compare
   returned catalog/evidence hashes with the operator receipt. Query a known
   approved passage and compare its exact locator/hash. Empty search is not a
   health failure; mismatched pins or altered corpus bytes must refuse.
5. **Recover:** stop the client/server, preserve existing immutable bundles, and
   reopen with the same verified pins. Never repair a failed hash silently or
   replace it with a newly computed pin. Roll back to the previous approved
   package and disclosure snapshot if validation fails.
6. **Record:** retain source commit, patch/wheel digest, dependency versions,
   snapshot pins, client/transport, executed test commands and outcomes. Distinguish
   installed, locally exercised, remotely deployed and production accepted.

The local integration test also retrieves a real synthetic passage, calls the
existing deterministic `ciw.services.invoke("ncp_demo")`, and retains both with the existing
owner-scoped Store; reopening preserves the exact citation and a different owner
is refused. That is a synthetic composition check. The accompanying reference
is explicitly **not** claimed as scientific support for the simulated output.
It is not evidence of a live ODSP gateway call or an authorized real instrument.

### Existing cloud deployment hold

The inspected deployed `ncp-control` is an older October 17 adapter/image, not
this integration or current ODSP main. The historical private-host acceptance
record has an unresolved `outcome_unknown` durable transaction/recovery fence.
Even its GET `/api/status` can mutate a checkpoint. Do not probe it as a supposedly
read-only health endpoint, repoint its storage, clear its fence, or overwrite its
image as part of this local runbook. Host recovery and deployment acceptance
need their own explicit approved procedure and exact immutable source/image.
