<!-- ncp-folder-entry -->
# MCP entry

- path: `tools/odrp-private-host-candidate`
- class: `navigation`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

Tasks in this folder, none of them the default: `panel`, `tools`.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# ODSP / ODRP isolated private-host candidate

This candidate implements a proposed private Linux/POSIX single-writer profile. It has not been deployed or qualified on Cloudflare. The historical paused host, storage and unknown transaction remain unchanged.

Start with the [release contract](../../docs/PRIVATE_RELEASE_CONTRACT.md), [operations runbook](../../docs/PRIVATE_HOST_OPERATIONS.md) and [executable qualification gaps](../../release/private-host-gaps.json). These distinguish local software evidence, installed containers, hosted storage/identity and individual capability qualification.

The candidate uses current per-principal tool, scientific-operation and resource grants; installed source-bound release verification; owner-isolated records in one deployment checkpoint; epoch-fenced custody; bounded admission, archives, journals and timeouts; and explicit unknown-outcome holds. Scientific calls use the actual installed principal MCP stdio service. ODRP HTTP disclosure delegates to the same reference host/facade; the HTTPS wrapper is not native MCP Streamable HTTP.

There is no default production hostname, singleton, credential or grant. The trusted launcher must supply explicit release, owner, compatibility generation, current policy, approved resource bindings and limits. Policy/credentials are not restored from checkpoints. Reference text, discovery and successful synthetic tests grant no execution authority.

`scripts/build_private_release.py` assembles a clean context outside the checkout from the exact committed source, builds the wheel twice offline and records source/package/dependency/service-file digests. Dockerfiles consume that context with offline dependencies; retain a separate post-build image receipt. No wheel or credential is checked into this folder.

`scripts/check_private_host_qualification.py` runs mandatory installed runtime, deterministic SDK, official MCP, mounted-reference, load/soak, adapter-process, gateway and storage-fault checks. It refuses missing, failed or skipped required evidence. Local SQLite/filesystem substitutes do not establish Durable Object or R2 qualification. Workflow presence is not a hosted CI result.

Recovery preserves uncertain transactions and old working directories. Owned restore currently rehomes only the exact current committed checkpoint while incrementing the authoritative epoch; stale historical backup rollback and empty-store disaster recovery are unsupported. It never clears the historical paused portal's fence or migrates that state.

Do not deploy, expose the internal adapter port, change account/network settings, issue credentials, remove holds or publish images on the strength of this folder. Obtain the separately scoped operational approvals and actual hosted evidence described in the runbook.
