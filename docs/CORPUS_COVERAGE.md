# Frozen intake coverage

`python -m ciw.corpus_coverage --selection selection.json --output NEW_DIRECTORY`
prepares a create-once local metadata coverage snapshot through the existing
`ProtoCorpus` compiler. This command accepts host-selected paths only; no new
MCP authority, acquisition provider, database, or evidence representation is added.

The selection has schema `ciw.corpus-coverage-selection.v1`,
`authorizes_execution: false`, and `batches`, each containing an
`intake_manifest` and `verification` path. It accepts the same two retained intake
receipt shapes used by `corpus_refresh`, for PDF metadata. At most 16 batches and
1,024 intake records are considered. Every source ID appears once in the coverage
record, either with a frozen native catalog/record pin or an explicit hold.
Conflicting identities, execution claims, and contradictory duplicate declarations
refuse the complete build. Missing hashes, unsupported representations, missing or
failed historical verification, and oversized sources remain visible holds.

The builder partitions by retained topic, at no more than 64 IDs and 256 MiB of
original declared bytes per partition. Exact duplicate groups stay together, keep
all source IDs, and retain both declared and equal-byte relationships. Different
byte versions keep their own hashes and related-source observations; a relationship
is not an adjudication that editions are interchangeable. Only explicit
`Knowledge Kernel/`, `Notations Kernel/`, and `02_Source_Library/` root aliases
are stripped from topic names. Original category/destination observations survive.
No historical files move or change. No archive is decompressed and no PDF is parsed.

`coverage.json` contains source hash, destination, original category, title basis,
review extent, bibliographic gaps, related identities, external source role,
rights state, and catalog pin or hold for each ID. Full-text admission remains held
for every source; a frozen catalog is not passage evidence. Financial-computing
references retain their external-reference role and do not become engineering
workloads. Bibliographic transcriptions are not verified publication identities.

`provenance/` retains exact selection, intake and verification bytes. Normalized
native verification records explicitly state their historical assertion scope.
They do not claim a fresh source-byte hash check, cloud observation or move.
`Coverage.open(directory, expected_ref)` verifies the coverage pin, provenance
bytes, each native catalog derivation and source-record links. It offers exact-ID
`resolve` and native metadata-token `search`, with rights/full-text disclosures.
Search coverage and passage relevance are separate; no relevance claim is made.
Each `topic-*/catalog` can also be bound through the existing read-only knowledge
MCP configuration using its reported catalog pin. Nothing is activated automatically.

This local pin model does not authenticate an administrator who can replace both
pins and records. Grant state, scientific acceptance, rights adjudication, original
source authenticity, and source availability remain outside this metadata reader.

Tests use synthetic metadata and independent counts, checking 83 distinct IDs /
82 hashes, 64/19 partitioning, duplicate and related versions, exact-title/ID
retrieval, negative queries, unknown rights, explicit holds, conflicting authority,
provenance tampering, repeatable compilation and refusal to overwrite snapshots.
Private actual-source coverage and qualification records must remain outside the
repository and are not authorized for public disclosure by running this command.
