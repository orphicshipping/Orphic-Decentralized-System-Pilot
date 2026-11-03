# Python client context selector

This Python library/reference client uses the actual ODSP campaign capability
descriptions. `SOURCE_REVISION` identifies the `c044bbdf` schema baseline; the
installed release marker identifies the built commit. Catalog identity also
includes actual current schemas and host bindings, rather than that label alone.
It bounds the schemas included in a client's model context. It does not implement
server session loading/unloading, invoke a tool, write skills, retain permissions,
or alter discovery, imported instruments, receipts or state semantics.

Local selection stays entirely in the embedding client. Two selector instances
have independent selections, and selection never changes server discovery.

## Supported client contract

Supported: Python 3.11+ in this repository's installed `ciw` package, synchronous
in-process reference client. No proprietary client integration, remote identity
attestation, async SDK transport integration, or hosted qualification is claimed.
The embedding application owns the model context update and independently sends
any later tool invocation through its existing authorized server path. A previously
exported dictionary cannot be retracted from a consumer; the application must
replace its context after refresh failures or catalog changes. It must never treat
selection as permission or use it to skip server authorization.

Trusted launcher code binds an existing `PrincipalFacade` or `AuditedPrincipalHost`:

```python
from ciw.context_catalog import for_principal_facade
selector = for_principal_facade(facade)  # existing host-owned facade
page = selector.list(category='control', limit=8)
detail = selector.describe(page['items'][0]['name'])
context = selector.select([detail['tool']['name']])
# context['tools'] contains exact server-derived schemas, sorted by tool name.
```

The constructor reads one detached snapshot from `facade.policy.snapshot()`,
then calls the current `PrincipalFacade.descriptions()` on a discovery-only shallow
copy with a snapshot-bound policy reader. This reuses existing grant filtering
without changing the live facade. The copy is never used for invocation.
Identity includes that snapshot, bound principal/tenant/reference scopes, immutable
capture digests, proof registry version/content, runtime bindings and process
policy. An audited wrapper adds its journal binding and operation allowlist;
discovery checks its fixed principal identity before and after reading. The
policy lock does not control external writers;
one snapshot avoids mixing policies during A→B→A reloads. Identity, tenant, paths and pins
are never selector operation arguments. This adapter does not change the facade
or register any additional tools. Existing discovery may expose a tool whose
argument-level execution permissions are narrower; discovery is never a grant.

Other embedding hosts can bind `ContextSelector(read_authorized)` in trusted code.
That zero-argument callback must authenticate using host state and return a
coherent `AuthorizedCatalog(revision, families)` on every read, or raise. Revision
must change on membership/identity/policy changes even if schemas stay identical.
This callback and the constructor are host APIs, not model-callable operations.
Do not bind the unfiltered offline source registry to a live user.

## Operations and failure behavior

- `categories()`: available categories and counts from the current authorized view.
- `list(category=None, cursor=None, limit=16, max_bytes=262144)`: bounded summaries.
- `search(query, ...)`: bounded case-insensitive literal substring search over tool
  name and description, in deterministic name order; no semantic ranking claim.
- `describe(name, ...)`: exact schema plus its deterministic serialized SHA-256.
- `select(names, ...)`, `deselect(names, ...)`, `selected(...)`: exact selected
  schemas, sorted by name. Failed selection/budget checks do not partly commit.

Each operation refreshes the host view. A changed version, policy or schema clears
selection and raises `StaleCatalog`; rediscover, then explicitly reselect. A refresh
failure clears selection and propagates the error. Pagination cursors bind catalog
identity, category and query; stale or mismatched cursors are refused. Unknown
categories/tools are refused. Metrology/GIS/finance/trade saved observations use
the existing `domain_read` family. No separate live feed, trading operation or
new numerical engine is created by this selector.

Names, descriptions, annotations and schemas remain untrusted data, never
instructions. No ranking implies suitability, qualification or scientific validity.
No selection result authorizes execution.

## Limits and serialization

Output budgets count the complete canonical JSON envelope, including metadata,
using `serialize()`: UTF-8 with ASCII escapes, sorted object keys, compact
separators, finite JSON values only. Consumers must use those bytes for this byte
contract; other serializers have different sizes. Schemas are preserved as exact
JSON values, not their original whitespace/key ordering. Large schemas are refused
rather than truncated. Only list/search descriptions are summarized to 160
Unicode characters; their encoded bytes still count against the envelope budget.

Limits: 128..262144 output bytes, 1..64 results per page, 512 UTF-8 query bytes,
64 selected tools, 1024 catalog tools, 64 families, 4 MiB catalog bytes. Existing
`json_tree` depth/text/container bounds also apply. No tokens or tokens-per-byte
ratio are estimated. Callback execution/resource bounds remain the host's duty.

## Source inventory and reproduction

`source_registry()` reuses existing control descriptions and profile manifests.
The source inventory is **86 schemas**, not a deployed or authorized visible count:
control 35, reference 7, evidence 5, logistics 5, recompiler 3, schematic 5,
clocksync 3, state 8, NISE 5, intake 4, proof 2, domain_read 4. Source names and
schemas are not aliased or renamed. Trusted launchers replace host bindings under
the facade policy lock; a single before/after snapshot refuses observed drift.
Host mutation outside that coordination contract is not made atomic by a hash.

```sh
python -m venv /tmp/odsp-context-client
/tmp/odsp-context-client/bin/python -m pip install /absolute/path/to/checkout
cd /tmp
/tmp/odsp-context-client/bin/python /absolute/path/to/checkout/examples/context-selector/offline_inventory.py
```

The installed offline example reports serialized source-schema array bytes and a
single selected reference schema, clearly labeled as offline inventory. It makes
no tool calls or network requests. Its chosen schema is an arbitrary deterministic
example, not a selection-quality benchmark.

Run source-derived tests:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_context_selector.py -v
```

Tests cover all 86 exact schemas, deterministic order/identity, authorization
filtering, actual `PrincipalFacade` revocation and independent server refusal,
membership refresh, malformed/failed catalog reads, stale cursors, Unicode/byte
budgets, atomic selection, concurrent clients and unchanged existing server
`tools/list`. The source inventory test does not establish live upstream access.

The original patch was prepared against `cef7d007`, then reconciled with the
campaign's capture/trust profiles and audited dispatch. Combined tests include
changed captures/registries and old receipt disclosure. Local compatibility
checks do not establish production integration or live upstream access.
