# Work Order: Next Development Leg — Consolidation Close-out and Qualified Research Baseline

**Project:** Notations Control Protocol (NCP) / Computational Instrumentation Workbench  
**Repository:** notationsystems/Notations-Control-Protocol  
**Date:** 2026-10-17  
**Prepared from:** Audit of open PR stack, `docs/PR_CONSOLIDATION_20261017.md`, `docs/QUALIFIED_RELEASE.md`, `docs/CONSOLIDATION.md`, `docs/UNIMPLEMENTED_DIRECTIONS.md`, and current open PRs  

**Status:** Draft for review and assignment  
**Target completion window:** 2–4 weeks (dependent on full-history CI and private-provider access)

---

## 1. Objective

Close the 2026-10-17 consolidation candidate, land the remaining high-priority increments against a single tip, and establish one immutable qualified research baseline.  

This leg does **not** expand into physical equipment integration, OpenUSD runtime reload, the learning/reasoning layer, or new domain tools. Those remain subsequent legs after a clean baseline exists.

**Success criterion:** One selected commit SHA on `main` (or an explicit release-candidate branch) for which:
- The consolidation ledger is updated and the remaining open PRs are either merged, explicitly deferred, or closed with rationale.
- Selected public CI, installed-wheel, and operator-journey checks have been executed and retained.
- Capability and qualification boundaries are recorded without over-claim.
- The repository is in a state where further work can be added as small, targeted increments rather than large stacked drafts.

---

## 2. Scope

### In scope
1. Finish review and integration of the consolidation candidate described in `docs/PR_CONSOLIDATION_20261017.md`.
2. Resolve or disposition the remaining open PRs (especially #240, #242, #232, and the earlier NCP/MCP/GaugeLink stack).
3. Execute and retain the full-history and installed-wheel gates that the consolidation ledger notes as still open locally.
4. Produce an updated capability/qualification snapshot for the chosen SHA.
5. Clean up long-lived branches that have been fully reconciled.
6. Ensure the cloud development setup (`docs/CLOUD_DEVELOPMENT.md`) and operator quickstart remain accurate against the consolidated tip.

### Out of scope (explicitly deferred)
- Physical instrument / equipment integration and the temperature pilot hardware.
- OpenUSD runtime reload, binary formats, or scene import.
- Learning/reasoning surfaces (`history` / `learn` / `explore`).
- Broader computational kernel families or general sensor fusion.
- Remote multi-user authenticated service or hard real-time paths.
- New domain instruments beyond what is already in the consolidation candidate.

---

## 3. Background and current state

- The repository has a strong core of retained investigations, explicit contracts, and conservative claim boundaries.
- A consolidation candidate exists that reconciles approximately 80 open pull requests. Local checks on that candidate reported large numbers of passing cases with explicit skips.
- Full-history modules (`test_monorepo.py`, `test_module_update.py`), private-provider qualification jobs, Windows/SP1/hardware gates, and some native runtime paths remain open acceptance gates.
- Open PRs continue to exist (including recent ones for MCP operator console / operational records, built-in numerical operations, and BIM clearance).
- The dominant risk is integration and qualification debt, not missing core architecture.

---

## 4. Tasks

### Task 1 — Stabilize the consolidation candidate
**Owner:** TBD  
**Priority:** Critical  

- Identify the exact tip SHA of the consolidation candidate.
- Run the full-history consolidation workflow (or equivalent local full-history checkout) and retain the results.
- Confirm the isolated-module regression numbers and the installed-wheel NCP contract run.
- Update `docs/PR_CONSOLIDATION_20261017.md` (or a successor ledger) with the final SHA, check results, and any remaining blockers.
- List every still-open PR and assign a disposition: merge into candidate, defer with reason, or close as superseded.

**Acceptance:** Ledger updated; remaining gates either closed or explicitly named with owners.

### Task 2 — Land priority increments against the candidate
**Owner:** TBD  
**Priority:** High  

Focus on the currently active open PRs that are not already inside the consolidation ancestry:

- #240 — Add seven built-in numerical operations through NCP and MCP.
- #242 — Connect operational records to engineering investigations through MCP and console.
- #232 — Add bounded BIM clearance inspections through retained execution and local MCP.
- Any residual MCP operator-console work (prior #231 direction).

For each:
- Rebase or retarget onto the consolidation tip.
- Verify it does not regress the selected NCP/MCP/contract suite.
- Merge or explicitly defer.

**Acceptance:** These increments are either on the tip or have a recorded deferral with rationale.

### Task 3 — Qualification snapshot for the chosen SHA
**Owner:** TBD  
**Priority:** High  

- Select one immutable commit after Tasks 1 and 2.
- Run (or collect) the public CI lanes, installed-wheel checks, and the three operator-journey precursors described in `docs/QUALIFIED_RELEASE.md`.
- Retain evidence (reports, wheel digests, journey outputs) in a dated directory.
- Update or append to the capability ledger / release-scope records so they reflect this SHA.
- Explicitly list what remains un-qualified (private providers, SP1 beyond the registered heat profile, Windows/hardware, physical validation).

**Acceptance:** A retained evidence package exists for the chosen SHA; the ledger distinguishes implemented / installed / qualified.

### Task 4 — Branch and PR hygiene
**Owner:** TBD  
**Priority:** Medium  

- Archive or delete long-lived branches whose changes have been fully reconciled into the tip (after confirmation).
- Close superseded PRs with a link to the consolidation ledger or the merging PR.
- Ensure `docs/CLOUD_DEVELOPMENT.md` and the quickstart still match the consolidated install and command surface.

**Acceptance:** Branch count reduced; open PR list reflects only active or deliberately deferred work.

### Task 5 — Connective-tissue readiness (optional, if capacity remains)
**Owner:** TBD  
**Priority:** Low (stretch)  

If the baseline is stable early, begin design notes (not implementation) for the highest-leverage items from `docs/CONSOLIDATION.md`:
- Data-only profile and capability registry.
- Typed composition graph sketch that reuses existing operation/record contracts.

These are design-only in this leg. Implementation belongs to the following leg.

---

## 5. Deliverables

| Deliverable | Location | Notes |
|-------------|----------|-------|
| Updated consolidation ledger | `docs/PR_CONSOLIDATION_20261017.md` or successor | Final SHA, check results, PR dispositions |
| Chosen baseline SHA | Recorded in ledger + release-scope / capability ledger | Immutable |
| Retained evidence package | `results/release-candidate-YYYYMMDD/` or equivalent | CI, wheel, journey outputs |
| Closed or deferred PR list | GitHub + ledger | With rationale |
| Branch cleanup record | Short note in ledger or issue | What was archived |
| (Optional) Design notes | `docs/` | Capability registry / composition sketch only |

---

## 6. Dependencies and risks

| Risk | Mitigation |
|------|------------|
| Full-history checkout unavailable locally | Use hosted consolidation workflow; do not bypass source-integrity checks |
| Private-provider tokens restricted | Keep those jobs refused on ordinary PRs; schedule them only on trusted provisioned workflows |
| Further stacked drafts appear during the leg | Freeze the candidate tip; new work targets the post-baseline main |
| Over-claiming qualification | Retain the existing “historical evidence / explicit skips / not physical” framing in all new docs |
| CI instability on large tree | Prefer the isolated-module regression runner already added in the consolidation |

---

## 7. Acceptance criteria for the leg

- [ ] One selected commit SHA is declared the qualified research baseline candidate.
- [ ] Consolidation ledger reflects that SHA and the disposition of all previously open PRs.
- [ ] Selected public CI + installed-wheel + operator-journey evidence is retained for that SHA.
- [ ] Remaining qualification gaps (private providers, SP1, hardware, physical) are explicitly listed, not implied closed.
- [ ] Active open PR count is substantially reduced; new work can be proposed as small increments against the baseline.
- [ ] Cloud development and quickstart docs are consistent with the tip.

---

## 8. Suggested sequence

1. Stabilize and fully check the consolidation candidate (Task 1).  
2. Land or defer the priority open PRs (Task 2).  
3. Freeze SHA and collect qualification evidence (Task 3).  
4. Perform branch/PR hygiene (Task 4).  
5. (Optional) Design notes for connective tissue (Task 5).  

Do not begin physical, OpenUSD, or learning-layer work until the acceptance criteria above are met.

---

## 9. References

- `docs/PR_CONSOLIDATION_20261017.md`
- `docs/QUALIFIED_RELEASE.md`
- `docs/CONSOLIDATION.md`
- `docs/INTEGRATION_COVERAGE.md`
- `docs/UNIMPLEMENTED_DIRECTIONS.md`
- `docs/CLOUD_DEVELOPMENT.md`
- `docs/QUALIFICATION_PROGRESS.md`
- Open PRs #240, #242, #232 and the NCP/MCP stack

---

**End of work order.**  
Assign owners, confirm the consolidation tip SHA, and begin with Task 1.
