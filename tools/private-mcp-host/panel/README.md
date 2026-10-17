# NCP private live workbench

The home page is an operator client of the genuine Python NCP control MCP server.
It discovers tools and numerical capabilities at runtime. The retained repository
explorer remains available at `/explorer.html`, explicitly labeled an offline
catalogue; its declarations are not execution grants.

The owner-private Sites access policy protects the panel and proxy. The server-only
`NCP_BACKEND_TOKEN` secret authenticates requests to the dedicated Cloudflare
container host. Never put that secret in browser code, source, or the manifest.
Do not make this Site public: the proxy relies on the private platform boundary.

The Python host uses the installed wheel from NCP revision
`c47a8621cd97f8d46afe526ed15534111fc6e347`. Fourteen MCP tools expose nine numerical
operations. The unchanged synthetic profile grants the seven new operations,
limits execution attempts to four, and permits no arbitrary parameter edits.
The first hosted acceptance run consumes one attempt. Discovery does not grant
execution, qualify a provider, or establish physical metrological traceability.

Requests are serialized. Each transaction opens the retained MCP profile and
closes its owned stdio session; submitted numerical jobs finish before checkpointing.
The host commits its checkpoint to private R2 storage before returning success.
Uncertain transactions fence later requests rather than replaying work. The
container has private networking, no outbound Internet at runtime, one-instance
maximum, a non-root Python process, and a two-minute inactivity timeout.

Build and check:

```sh
npm ci --ignore-scripts
npm run build
npm test
```

The build uses the Sites Worker ESM artifact layout: `dist/server/index.js` and
`dist/.openai/hosting.json`. Package only those outputs, not `node_modules` or the
old static `dist/index.html`. From the source root, package with
`tar -cf private-panel.tar .openai/hosting.json dist/server/index.js`.
Preserve project ID and owner-private audience.
Source synchronization uses short-lived credentials through hidden stdin.

No browser-control facility was available in the provisioning environment. DOM,
proxy, MCP and HTTP acceptance checks are distinct from visual browser verification.
Restart/profile changes, arbitrary shell, package installation and physical device
commands are deliberately not exposed in the panel. Native external Streamable
HTTP MCP transport is not added here; this client uses the genuine stdio MCP server
behind an authenticated HTTP adapter.

Original NCP code and generated interfaces retain AGPL-3.0-or-later licensing.
The previous static page remains recoverable in Git history and retained source.
