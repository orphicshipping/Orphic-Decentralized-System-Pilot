# NCP / GaugeLink architecture implementation sequence

Source: the operator-approved knowledge-base architecture review dated 7 October 2026. Literature recommendations are design inputs; they do not qualify an installed instrument chain.

## Delivered in this increment

The bounded covariance-scenario MCP profile connects an exact retained scalar Station investigation to the shared Board, semantic registry, Session, Needle, RecoveryStore and official SDK adapter. It preserves all four original measurement occurrences, reuses the unchanged basis result and retains an independently audited candidate for review. CLI and MCP share domain behavior. [Contract and usage](GAUGELINK_MCP_REFIT.md).

No new numerical dependency or workflow engine was introduced. GaugeLink remains the acquisition owner; NCP owns analysis and retained proposals. Historical persona and measurement schemas remain unchanged.

## Remaining deliveries and exit criteria

| Order | Work | Required evidence before completion |
| --- | --- | --- |
| 2 | PDF representations and source applicability | Bounded parser selected and version-pinned; original bytes hashed; page and text/table locators; original-to-representation-to-span links; extraction warnings and unsupported/OCR cases retained. Byte changes invalidate selection. Identical PDFs share source identity rather than counting as independent corroboration. Review notes remain separate from source assertions. |
| 3 | Calibration-document interpretation | One pinned DCC schema/profile and local fixtures; separate structural validity, issuer authentication, quantity/unit/channel/range/time applicability and review outcomes. Missing trust data stays unknown. Unsupported documents retain bytes without implying calibration. |
| 4 | Reporting fidelity and numerical qualification | Versioned measurement display exposing original token/reporting scale with no implied uncertainty from trailing zeros; declared rounding; prespecified tolerances for reference cases, correlated means, cancellation, ill-conditioned inputs and provider substitution. Preserve raw bytes and existing schema identities. |
| 5 | Product compatibility and domain adapters | Discovery mapping from seven historical Link persona IDs to the four current product profiles without renaming saved IDs. Add each domain only with explicit source, timing, range and hardware evidence. Separate capture integrity, calibration applicability, uncertainty, computation status and acceptance in the operator view. |

These are tracked design tasks, not implemented capabilities in this increment. DCC authenticity and installed-chain qualification cannot be established from code alone. The existing missing-identity and evidence-gap states remain authoritative.

## Follow-on design decisions

- Gain/offset refits require a derived request referencing the same immutable capture. Do not edit coefficients inside the existing source or carry a certificate assertion over to new coefficients automatically.
- A source applicability record should bind source hash and exact locator, supported claim, quantity/unit, instrument class, operating domain, assumptions, uncertainty meaning, contradictions and independent review state. Search relevance cannot grant applicability or execution authority.
- Evaluate pinned PyDynamic/MetroloPy numerical cases when timing and uncertainty prerequisites exist. Compare against NCP native contracts before adopting a provider. Study agentMET4FOF message/buffer patterns without adding a competing workflow owner.
- Remote OAuth/Cloudflare access may later front the same tools. Transport authentication, scoped operation grants and candidate acceptance remain independent. Existing access-denied configuration must not be weakened to demonstrate connectivity.

## Verification record

The code includes an installed SDK journey and CLI cross-check, source/authority/refusal regressions and independent arithmetic criteria. A local test report records the actual results for this increment. No new live Drive ingestion, remote deployment, calibration acceptance or hardware acquisition is included.
