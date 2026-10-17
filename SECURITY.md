# Security Policy

## Supported versions

Security fixes are evaluated against the current supported NET development line and any explicitly qualified release identified in [docs/QUALIFIED_RELEASE.md](docs/QUALIFIED_RELEASE.md). Historical commits, superseded candidates, archived imported components, and unqualified experimental branches are not automatically supported.

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in a public issue, discussion, pull request, commit message, log excerpt, retained evidence artifact, or example dataset.

Report security-sensitive findings privately to the project maintainers through GitHub's private vulnerability reporting interface when it is available for this repository. If private reporting is unavailable, contact the repository owner through a private channel and provide only the minimum information necessary to establish contact before transmitting exploit details, credentials, private provider material, or sensitive evidence.

A useful report should include:

- the affected NET revision or release;
- the affected component, provider, adapter, workflow, or interface;
- prerequisites and deployment assumptions;
- a reproducible description or proof of concept;
- observed and expected behavior;
- potential impact on confidentiality, integrity, availability, identity, provenance, execution authority, or verification;
- whether the issue crosses from observation into command or physical-control surfaces;
- any known mitigations.

Do not include production credentials, access tokens, private provider source, customer data, confidential artifacts, or unnecessary personal information.

## Security boundaries

NET spans scientific computation, retained evidence, external providers, interactive clients, deployment tooling, and cyber-physical integration. A security report is especially relevant when a defect can:

- alter or forge retained evidence, identities, provenance, runtime bindings, verification results, or qualification records;
- execute provider code or commands outside the declared runtime or authorization boundary;
- bypass refusal behavior, stale-state checks, signature or digest checks, or evidence immutability assumptions;
- expose workspace contents, credentials, private provider material, or sensitive inputs through logs, exports, artifacts, network services, or client surfaces;
- cross a declared read-only, observation, supervisory, closed-loop, or safety boundary;
- cause unauthorized physical actuation or weaken a safety interlock or safety authority;
- introduce dependency, build, release, or supply-chain compromise.

Scientific disagreement, model inadequacy, missing physical qualification, numerical limitations, and documentation errors are not automatically security vulnerabilities. They should use the normal issue or contribution process unless an attacker can exploit them to violate a security boundary.

## Cyber-physical safety

Security acceptance does not establish physical safety, metrological validity, process qualification, or permission to control equipment. NET must preserve the separation between evidence, operation, execution, result, verification, and physical authority. A software fix does not authorize deployment to machinery.

New hardware integrations should begin at the minimum necessary authority and must not silently acquire command privileges. Safety PLCs, emergency-stop systems, certified protective functions, and equivalent independent safety authorities remain outside NET unless separately engineered and qualified for that responsibility.

## Coordinated disclosure

Maintainers will triage credible reports, reproduce them where possible, determine affected versions and boundaries, and coordinate remediation and disclosure. Public disclosure should wait until a fix or documented mitigation is available, unless earlier disclosure is required by law or necessary to prevent imminent harm.

Security fixes should include regression coverage appropriate to the affected boundary. If a required provider, platform, hardware configuration, or qualification environment is unavailable, the unresolved gate must remain explicit.

## Dependencies and imported components

NET contains or interfaces with imported modules and external scientific, graphics, simulation, provider, driver, and runtime dependencies. Upstream vulnerabilities should be reported to the responsible upstream project when the defect originates there. NET-specific integration defects, insecure pins, unsafe adapter behavior, or boundary failures should also be reported here.

Licensing and distribution obligations remain governed by [docs/LICENSING.md](docs/LICENSING.md). Security reporting does not grant permission to redistribute private or restricted source material.

## Public issue tracker

Use the public issue tracker for non-sensitive bugs, reproducible integration failures, numerical issues, documentation defects, feature proposals, and already-public security advisories. Remove secrets and private artifacts before posting.
