# Risks and weaknesses after baseline close-out

This assessment belongs to code `312a37ba613a0b57ea027b8d00df5b3b4fe08d69`.
The evidence was collected across 2026-10-17/18 UTC. Owners below are roles;
no person is implied to have accepted an assignment outside this work order.

| Risk | Current evidence | Action and owner |
|---|---|---|
| Integration and qualification debt | All 514 isolated modules pass: 19,155 passing cases, 1,170 explicit skips and no failures/errors. Both original ancestry-sensitive modules pass all 176 cases without skips. The same wheel passes 531 NCP and 209 priority cases. Hosted required CI remains queued/pending; private/native/platform gates remain separate. | The local history gap is closed for the tested selected source closure. The repository maintainer/runner administrator must complete same-SHA hosted lanes; provisioned-provider operators own their missing qualification evidence. |
| Workflow proliferation | The selected tree contains **156 workflow files and 192 top-level jobs**; 14 workflows are required by the release policy and 13 refer to the private-provider token. No workflows are exact semantic duplicates when only their display names are ignored. **106 test modules appear explicitly in multiple workflow files**; this overlap can be intentional. The reported approximate 183 registered workflows could not be verified through the available workflow-collection endpoint. | Maintain the inventory and review overlaps by event, platform, provider, installed-wheel and artifact purpose. The CI maintainer should consolidate repeated setup or reusable jobs only after proving equivalent required jobs, permissions, refusals and evidence. No workflow is deleted based solely on shared test paths. |
| Branch hygiene | All **282 branches** are inventoried. All original 82 PRs are dispositioned. #244 and #139 subsequently merged; no PR was open at the final pre-publication observation. The evidence follow-up is a new review. **79 reconciled PR heads plus 156 exact candidate ancestors** are eligible for archival/deletion after confirmation. Eight inventoried current/release/transport branches and the new evidence branch are retained; 39 other heads are preserved for individual scope review. | The owner must confirm the pinned 235-head cleanup plan. Preserve archive tags, recheck every head and refuse any advanced branch. The repository maintainer owns the separate 39-head scope review. No original branch has been deleted or archived. |
| Complexity concentration | The numerical, operational and BIM increments coexist through existing operation/Session/MCP contracts. Integration checks found and repaired matrix-policy drift and a missing operational command review record. Full regression and installed boundaries now pass. | Require one focused operation/record owner per next increment, preserve explicit provider/grant contracts and run the affected installed and retained-source gates. NCP/MCP/GaugeLink/manufacturing maintainers own future boundary refactors; this leg introduces no broad refactor. |
| Entry barrier | Cloud dependency instructions now include the pinned numerical/schema extras; repository links and release policy use the current repository. A selected-wheel first-use run and the existing installed operator gate passed. | Start with the short installed first-use guide, then choose a specific profile. Documentation owners should keep the minimal entry path tied to an exact wheel and link detailed contracts only at the next decision. |
| Private dependencies | Thirteen workflow files reference the scoped provider token. Missing or untrusted credentials continue to refuse access; PR events do not gain secrets. Public retained sources allow the verified local ancestry tests, while private scientific paths remain separately provisioned. | Trusted provider operators must supply access only through existing approved event/ref boundaries. Track each missing provider/profile independently. Do not replace it with a stub, widen the token allowlist or infer qualification from a skip. |

The workflow and branch inventories are review evidence, not new runtime
registries. Remaining SP1 profiles, native Windows, hardware, acquired-data
campaigns, physical validation and remote multi-user service retain their
original qualification obligations. Evidence review and branch cleanup remain
separate maintainer decisions.

The temporary validation ref and canonical release ref both queued runs for the
same selected SHA. This duplicate validation set is recorded as CI cost debt;
no further pushes to the temporary ref are planned. The CI maintainer should
retain the latest qualifying same-SHA evidence per workflow while removing
redundant queued work through authorized controls. The available interface
does not expose workflow cancellation, and the release evaluator is not relaxed
to ignore a newer non-passing attempt.

The final main observation adds one guarded native-history transport workflow: **157 workflow files / 193 jobs** on main, compared with the tested SHA's 156/192. It also adds source-history recovery scripts and bundle pieces. It does not alter the 21-module import manifest or qualify the separate 23-module target. Original repositories and native transport refs must remain available until their independent publication/retirement obligations are resolved.
