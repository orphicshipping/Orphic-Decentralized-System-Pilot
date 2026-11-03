# Offline domain record inspection

`ciw.domain_read_profile` exposes exact host-approved metrology, GIS, finance and
trade records. It does not create another domain engine, query a market feed,
place trades, qualify measurements or admit evidence. Knowledge retrieval stays
in the existing separately granted ODRP reference profile.

The launcher supplies approved capture bytes, their independently selected
SHA-256, source identity (`system`, `revision`), principal and allowed domains.
The capture repeats these bindings. A matching hash establishes byte equality;
source authenticity remains `host_declared_unverified`. Caller arguments cannot
select an endpoint, file, identity, executable or grant.

| Operation | Closed arguments | Result |
| --- | --- | --- |
| `ncp_domain_read_describe` | `domain` | Format, capture provenance and limits |
| `ncp_domain_read_list` | `domain`, optional `offset`, `limit` | Bounded record identifiers |
| `ncp_domain_read_record` | `domain`, `record_id` | Complete immutable record and receipt |
| `ncp_domain_read_verify` | `domain`, `record_id`, `receipt_json` | Full content comparison against this host's capture |

Every operation is read-only, non-destructive and idempotent. There are no
resource or retention endpoints. `receipt_json` is raw JSON text so duplicate
keys can be rejected before a parser loses them. Recomputing forged digest fields
does not bypass comparison of the complete expected receipt.

Metrology reuses `metrology_contract.case`; GIS reuses
`geomatics_workflow.validate_request` and `net_geomatics.catalog.validate_output`.
These inspect saved contracts and do not execute the providers. Finance accepts
only `ciw.finance-observation.v1`; trade accepts only
`ciw.trade-observation.v1`. Prices/quantities are decimal strings with at most
18 integer and 8 fractional digits. Currency spelling is a three-letter host
declaration, not independently verified ISO registration. Financial observations
provide no investment recommendation, current-price claim or order authority.

Capture limits: 1 MiB, 64 records; each data record: 16 KiB. Requests are at most
65,536 serialized bytes; complete duplicated text/structured MCP results are at
most 262,144 bytes. A record is rejected at binding time if it cannot be read and
verified through those same limits. The existing bounded JSON depth, integer,
Unicode and numeric policies apply. Path-looking identifiers remain inert keys.

Use `ciw.services.open_domain_read(...)` from trusted launcher code, wrap it with
`policy.facade(principal, domain_read=host)`, then call `ciw.services.invoke` or
`ciw.knowledge_mcp.serve_stdio`. Current tool grants and optional `domain`
constraints are enforced on every call; constructor binding alone is not remote
authentication. All operations use the existing strict MCP frame reader.

`tests/test_domain_read_profile.py` exercises the four formats, existing validator
reuse, no provider/retention dispatch, full-content forgeries, current grants,
restart and actual official MCP subprocesses. This is local synthetic evidence,
not a deployed data-source or numerical qualification.
