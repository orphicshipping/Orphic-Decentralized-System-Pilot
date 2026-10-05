# Molecular reference source pack

See [the operating and evidence guide](../../docs/MOLECULAR_REFERENCE_DATA.md).

This directory retains four exact-byte NIST FEASST source/license files, nine
selected NIST computed reference values and fourteen manufacturer nominal facts.
The files are pinned in `catalog.json`; `catalog.sha256` anchors those bytes.
Keep an independent copy of the anchor. Source hash checks establish integrity,
not physical truth or third-party endorsement.

The original NIST files are unmodified; derived exports are explicitly marked
as NET transformations. See `sources/FEASST-LICENSE.rst` for NIST attribution
and distribution terms. Manufacturer source documents are not included and no
model-training license is inferred. Missing lot, uncertainty and measurement
timestamps remain missing. No simulator runs occur merely by reading the pack.
