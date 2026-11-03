# Companion source removal review

Status: **inventory complete; the proposed six-path removal scope is unresolved**.
No imported source, source history, licence, notice, runtime pin, or archive was
removed. This review makes no legal determination and grants no publication,
execution, collection or deployment permission.

The local review inspected HEAD `235ee45a11f744e8c801364965c07e9e3e13854f`
(root tree `993b5aa9249e50eca37a1fe4a9dafa04f6ee52fd`) on 2026-11-03. All
21 trees in `instruments/manifest.json`, containing 2,770 tracked blobs, match
their declared import trees. Every corresponding working file matches its Git
blob; the imported-source status was clean. Concurrent campaign work outside
these trees was not frozen by this inventory.

## The six proposed removals are not identified

No exact list of six paths or November 16 removal directive was found in the
supplied repository text or locally reachable Markdown/JSON history searched
for `six companion`, `6 companion`, `November 16`, `Nov 16`, or `2026-11-16`.
The filename history search for conversion directives and proposals also
returned no matching paths. This search does not establish the absence of an
external proposal or conversation.

[`MIGRATION_LEDGER.md`](../MIGRATION_LEDGER.md) explicitly records that the cited
`ODSP_LEGACY_CONVERSION_DIRECTIVE.md` and underlying decision transcripts were
not supplied. Its removal section records no file removals. The 21 imported
trees, six source identities mentioned elsewhere, and native runtime checkouts
are not interchangeable definitions of the requested six copies.

Before a removal patch can be concrete, its owner must identify the exact six
repository paths and whether “removal” means excluding them from one deployment
payload, deleting tracked copies in a forward commit, or retiring separate
repositories. These have different consumers and evidence requirements.
History rewriting and upstream repository deletion are not proposed here.
Until that scope is supplied, preserve the sources and their holds; do not
select six entries from the inventory by inference.

## Recorded source inventory

The sealed `modules.json` records the full import commit, current tree, runtime
commit, repository identity claim, licence paths and dependency manifests for
each row. Repository visibility and ownership fields are retained historical
claims, not fresh remote metadata.

| Module | Tracked path | Blobs |
| --- | --- | ---: |
| calibration | `instruments/measurement/calibration` | 18 |
| clocksync | `instruments/measurement/clocksync` | 18 |
| observability | `instruments/mathematics/observability` | 18 |
| state-inference | `instruments/inference/state-inference` | 24 |
| state-recompiler | `instruments/inference/state-recompiler` | 16 |
| faultsense | `instruments/inference/faultsense` | 18 |
| estimator-bench | `instruments/verification/estimator-bench` | 17 |
| sensor-design | `instruments/mathematics/sensor-design` | 21 |
| linear-dynamics | `instruments/mathematics/linear-dynamics` | 22 |
| sensitivity | `instruments/mathematics/sensitivity` | 62 |
| metrology | `instruments/measurement/metrology` | 29 |
| signal-processing | `instruments/measurement/signal-processing` | 13 |
| polygon-trajectories | `instruments/mathematics/polygon-trajectories` | 18 |
| surface | `instruments/mathematics/surface` | 96 |
| retrieval-agent | `instruments/composition/retrieval-agent` | 55 |
| yield-weighted | `instruments/resources/yield-weighted` | 38 |
| bim-estimator | `instruments/domain/bim-estimator` | 406 |
| real-time-globe | `instruments/views/real-time-globe` | 61 |
| frame-mapper | `instruments/representation/frame-mapper` | 998 |
| compute-runtime | `instruments/execution/compute-runtime` | 550 |
| flowstate | `instruments/domain/flowstate` | 272 |

The manifest includes 145 dependency metadata/lock files and 99 licence/notice
paths, including nested dependency notices. Static consumer references record
literal source paths, repository names and Python imports in SDK, tests,
workflows, scripts and documentation. This is not a complete dynamic call graph;
an absent literal match does not establish that a source is unused. Imported
source trees and host-selected runtime commits can differ. A removal review must
reproduce the affected exact-pin workflows and installed-package checks instead
of substituting a newer import tree or a copied module.

## FrameMapper source and notice findings

The exact current subtree is `ce1808ec8f8d54fb051a93ae14f3c869ecfeeddf`:
998 blobs and 11,128,525 content bytes. Of those, 589 files are under
`intel/node_modules`, comprising 66 package manifests; 15 files are Python
bytecode. These counts alone do not establish that a file can be deleted.
The permitted correction in [`THIRD_PARTY.md`](../THIRD_PARTY.md) changes the
stale 1,285 count to the exact 998-blob tree-qualified count only.

`frame-mapper/LICENSE` is the GPL-3.0 text, blob
`f288702d2fa16d3cdf0035b15a9fcbc552cd88e7`. The README's “Origin and license”
section attributes a fork of `simplifaisoul/osiris` and claims that an MIT grant
and retained notice apply to inherited code. The tracked non-dependency tree
contains no separately named upstream licence or NOTICE file. A text scan found
no MIT grant text beginning “Permission is hereby granted” outside
`node_modules`; the docs UI nevertheless displays “MIT Licensed” in
`src/app/docs/DocsClient.tsx`. These are source-level inconsistencies and an
unverified retained-notice claim, not a determination of rights or compliance.
The dependency MIT notices do not establish the inherited application's notice.

The imported `.dockerignore` excludes `LICENSE`, and the imported Dockerfile
does not explicitly copy that licence into its runner stage. No image was built
or inspected in this review, so this is a packaging-input observation only.
No licence text, attribution, imported Docker input or upstream notice was
created, replaced or removed.

## Retained collection and identity surfaces

The following are findings about retained source. They do not establish that
ODSP currently exposes these routes or that any live request succeeded.

| Surface | Verified local behavior or declaration |
| --- | --- |
| FrameMapper `src/app/api/entity/expand/route.ts` | Its `ALLOWED_TYPES` includes `person`; accepted requests proxy to the intelligence service. |
| FrameMapper `intel/server.js` | Defines `resolvePerson` and includes `person: resolvePerson` in `RESOLVERS`; other resolvers also construct person nodes. |
| FrameMapper `src/lib/stealthFetch.ts` | Builds randomized browser User-Agent and forwarded-IP headers. `src/app/api/weather/route.ts` calls it for EONET and GDACS. No such fetch was executed. |
| FrameMapper EDGAR source registry | `sec-edgar` has `adapter: null`. Configuration requires `PAYLOAD_SEC_UA_ORG` and `PAYLOAD_SEC_UA_CONTACT` when `PAYLOAD_EDGAR_ENABLED=1`; these names do not constitute a built connector. |
| FrameMapper identity | `identity.ts` declares `PayloadTerminal/0.1.0` and a historical `notationsystems/Payload-Terminal-V0` URL. `envCompat.ts` retains the `SEA_DOG_EDGAR_ENABLED` / `SEA_DOG_SEC_UA_*` aliases through its declared transition. No identity or contact value was supplied. |

These person-resolution and spoofed-header surfaces remain excluded from Domain
H integration. Broad README collection-policy statements do not prove that
every inherited route implements the exclusions.

The imported `compute-runtime/scout` is a fixture-driven evidence observer.
Its `ExtractedEntity.kind` is a string, and
`compute-runtime/evidence/admission.py:admit_referent` checks nonempty kind and
natural key, not an OSINT kind allowlist. A synthetic offline call accepted
`kind='person'`; it stored no referent and opened no network connection. This
structural helper is not a person-data approval or an NSL admission gate.
Domain H must retain its explicit `places/assets/infrastructure/imagery`
allowlist and inspect data independently of this general evidence type.

The PPDA acquisition adapter separately requires PPDA
`477d6cb454423a27543b16961d3b169709c40c31` and vendor SCOUT gitlink
`5e146d5924675cd7b6e1d1ed44fb39f5da012610`. The PPDA commit object was not
available in the inspected local Git database; the vendor commit was available.
No `edgar`, `resolvePerson`, or explicit person-kind declaration was found in
the vendor snapshot search. The newer imported SCOUT package is not a substitute
for that exact two-tree binding. No current external SCOUT/PPDA repository was
fetched, and no PPDA runtime qualification is claimed here.

## Ownership and route inconsistencies

[`CI_INBOX_RECOVERY.md`](CI_INBOX_RECOVERY.md) records the canonical FrameMapper
repository as `atomtrapping/Notations-FrameMapper-RunTime`, repository ID
`1347707016`; that is a historical verification, not a fresh remote check.
`spatial-provider.yml` and `spatial-godot.yml` use that recorded route.

Remaining differences include the active checkout target
`giasonpooni/Geospatial-Systems-Compiler` in `.github/workflows/rs-provider.yml`,
the `giasonpooni/Notations-FrameMapper-RunTime` metadata in both import indexes,
the FrameMapper README clone command, and the historical repository URL used
by `identity.ts`. FrameMapper `DOCKER.md` still instructs readers to clone or
pull `simplifaisoul/osiris`; its compose metadata also references upstream
assets. Attribution to the original project should remain distinct from an
instruction to run that project's image. None of these destinations, redirects,
visibility settings, credentials or image bytes was checked remotely or changed.
An active checkout repair must verify that its unchanged runtime commit exists
at the approved destination before it is accepted.

## Local sealed evidence

Evidence is under `/tmp/odsp-companion-removal-review/`. The read-only
`inventory.py` produced `source-files.json`, `modules.json`,
`license-notice-manifest.json`, `dependency-manifest.json`,
`static-consumer-references.json`, `framemapper-upstream-notice-search.json`,
and `scope-search.json`. `inventory-receipt.json` binds their hashes;
its SHA-256 is
`f4f8e51d968a2fd08162efee2e98341a6304d7b93daae7401f68767a71b2b4f5`.
`scout-kind-probe-receipt.json` separately records the synthetic structural test.
Local absolute paths are evidence locations, not deployment configuration.

The unresolved action is identifying the exact six paths and removal meaning.
It is **pending scope information**, not an attempted or denied deletion. The
inventory, narrow count correction and local structural probe are complete;
publication, live collection, source removal and legal clearance are not
outcomes of this review.
