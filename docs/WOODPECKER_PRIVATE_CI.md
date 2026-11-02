# Woodpecker preparation for the private ODSP candidate

This is a separate follow-on to reviewed candidate `d8842760`; it does not alter
that commit or PR264. It supplies a manual workflow, an exact-source driver and
failure tests. No Woodpecker server, agent, OAuth application, repository hook,
credential, registry publication or deployment is created by these files.

## Implemented local workflow

`.woodpecker/private-qualification.yaml` selects the **local** backend and the
`odsp-private-disposable` agent label. It is manual-only pending host/access
review. It runs seven sequential steps: source/pins and tool versions; existing
release-contract tests; the existing two-export offline wheel builder; fresh
offline installation from the selected cache; the existing seven-phase installed
client/fault qualifier; all four candidate Node suites; and an always-attempted
evidence summary. No deployment step or Docker command is included.

`scripts/run_private_woodpecker.py` requires the clean checkout to match
`CI_COMMIT_SHA` and the canonical `CI_REPO`, with a fresh absolute output outside
the checkout. Runner metadata is recorded as an assertion, not authenticated
provenance. Reusing an attempt or changing source/interpreter refuses. Missing,
failed, skipped or changed evidence cannot become a passing summary. The existing
command runner retains bounded logs, process-tree observations and exit statuses.
Its 1 GiB/96-process sampled ceilings are not a cgroup reservation or a sandbox.

Required host provisioning is Python3.12.14 with the existing private-profile
build/test dependencies, Node24.19.0, Git, uv and a complete already-provisioned
offline uv cache. Dependency versions are recorded by the existing builder;
cache use is not dependency-byte attestation. No package downloads occur in the
workflow. A missing cache entry fails. Each attempt creates a new installed
environment and new synthetic qualification state. Never reuse the qualifier's
interrupted transaction or clear its hold.

Trusted launcher variables:

- `ODSP_CI_OUTPUT`: new absolute attempt directory outside source and cache.
- `ODSP_CI_CACHE`: existing absolute offline uv cache, separate from source/output.
- `PATH`: preselected Python/build, Node, Git and uv executables.
- Woodpecker metadata: `CI_REPO`, `CI_COMMIT_SHA`, `CI_PIPELINE_EVENT=manual`, plus
  attempt/workflow identifiers. These are not new permissions or secrets.

Use the official checksum-verified Woodpecker CLI3.20.1 with
`--disable-update-check exec --backend-engine local --local --timeout 30m`,
explicit repository/commit/manual metadata and the two launcher variables via
`--env`. Run from a controlled working directory without `.env` or stored CLI
credentials. The CLI loads `.env` before flags. Give the invocation a selected
environment and empty configuration directory; do not inherit GitHub/cloud
credentials. This is command hygiene, **not isolation from the host filesystem**.
The local backend is suitable here only for these reviewed local commands.

`summary.json` and its file hashes describe `LOCAL_PIPELINE_PASS`, never a full
release. Preserve the whole output privately before disposing of a runner;
`installed/` and mutable contract test workspaces are excluded from the evidence
inventory. Evidence export/storage is an operator gate, not an implicit public
artifact plugin. Cancellation can prevent the final summary, so retain earlier
command receipts and CI logs too. They do not prove rollback.

## Existing gates remain intact

This workflow is additive. Neither GitHub workflow file nor existing release
collector policy changes. Woodpecker receipts do not satisfy the existing
GitHub-specific 56-job/45-artifact collector or its private additional gate.
The local pipeline does not run the full scientific suite, physical/provider
qualification or build/execute a new container. Those omissions remain explicit
in every summary. The previously reviewed d884 container receipt is historical
evidence for that exact candidate, not a container result for a follow-on commit.

The container commands already exist in
`.github/workflows/odrp-private-host-candidate.yml`: seal the dependency
wheelhouse; offline Docker build; record image/installed identity; run the actual
adapter/reference/discovery suites with network none, read-only root and UID10001.
Moving those commands to Woodpecker requires a separately approved disposable
runner and Docker access design. No Docker socket is mounted into this pipeline
or granted to a Woodpecker process here.

## Hosting and access decision still required

This temporary workspace is not an approved persistent CI host. Before any
server/agent or GitHub activation, approve the exact persistent controller,
private URL/TLS/ingress, durable database and backups, and disposable runner
location/resource limits. Keep build/test agents separate from deployment
credentials and private corpora. Use one workflow per disposable runner, no
privileged/network/volume trust grants, no secrets in PR jobs, and no shared
production Docker daemon. Agent labels are routing, not isolation.

Official v3.20.1 GitHub integration uses an OAuth2 application; its documented
GitHub App limitation must not be hidden by inventing a repository-scoped App.
Private access requests `user:email`, `read:org`, and `repo`; the `repo` OAuth
scope can be broader than ODSP. Select an already-approved dedicated identity
with appropriately limited repository access before authorizing it. Activating
the repository needs repository administration to install its webhook and allows
status updates and credentialed clone. Do not distribute clone credentials to
ordinary build steps. No such application, identity or hook is created here.

The server's HTTP listener (default `:8000`) and agent gRPC listener (default
`:9000`) must have explicit approved bindings; the agent health listener
(default `:3000`) also needs an explicit private/disabled selection.
Do not expose defaults. Agent-to-server transport needs TLS or an approved Unix
socket arrangement. Database choice, storage location, encryption/backup
custody, tested restore, log retention and upgrade/rollback ownership must be
recorded before persistence. Default SQLite state lives under
`/var/lib/woodpecker/`; upgrades migrate the database and do not create backups.
SQLite is a supported single-controller option;
PostgreSQL/Kubernetes are not requirements of this local preparation.

Use exact version/digest pins for any future server/agent images. The local CLI
archive checksum is not a container image digest. No registry digest is inferred
from a version tag. Full server/agent integration, webhook authentication,
private clone, status delivery, artifact retention and disposable-host teardown
must be exercised after the exact hosting/access approval. Local lint and exec
do not close these gates.

## Official source references

Checked against release3.20.1, source
`3f1bb0bd0db28e410ad797034d75780116122c91`, on 2026-11-02:

- [Release and assets](https://github.com/woodpecker-ci/woodpecker/releases/tag/v3.20.1)
- [Local execution](https://woodpecker-ci.org/docs/usage/local-execution)
- [Local backend](https://woodpecker-ci.org/docs/administration/configuration/backends/local)
- [Workflow syntax](https://woodpecker-ci.org/docs/usage/workflow-syntax)
- [Security boundaries](https://woodpecker-ci.org/docs/usage/security-boundaries)
- [GitHub forge](https://woodpecker-ci.org/docs/administration/configuration/forges/github)
- [Pinned linter source](https://github.com/woodpecker-ci/woodpecker/blob/v3.20.1/cli/lint/lint.go)
- [Pinned local-exec source](https://github.com/woodpecker-ci/woodpecker/blob/v3.20.1/cli/exec/exec.go)

Strict CLI lint treats repository trust flags as enabled and does not validate
cross-workflow dependencies. Local exec does not implement cross-workflow
`depends_on` ordering. This preparation deliberately uses one sequential
workflow; lint is syntax validation, not a security admission result.
