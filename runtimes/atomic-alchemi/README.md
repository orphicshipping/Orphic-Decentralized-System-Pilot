<!-- ncp-folder-entry -->
# MCP entry

- path: `runtimes/atomic-alchemi`
- class: `unbound`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Atomic ALCHEMI CPU environment

This is the optional `atomic.lj-energy-forces.v1` Session profile. The separate
`../nvalchemi-lj` environment and `scripts/check_nvalchemi.py` keep their own
`nvalchemi.lj-single-point.v1` contract.

`requirements-linux-cpu.txt` records the tested Linux x86_64 / Python 3.12.14
dependency versions, including the exact toolkit Git revision
`bf605f9191b2978fc865f4286d11e4481b679999` and toolkit-ops revision
`6702138e404d40ea79dec62f68c2fa67e858580e`. It selects CPU Torch explicitly.
These metadata pins are not publisher signatures or binary/hardware attestation.

Install the requirements in a dedicated environment, then install the NCP wheel
without changing those dependencies. Keep the bound source checkout clean and
put outputs outside it. The native worker requires the noneditable VCS toolkit
receipt; do not replace or forge direct_url.json metadata.

```sh
python scripts/prepare_atomic_nvalchemi.py --repository /CLEAN/NCP --revision FULL_SHA \
  --python /ENV/bin/python --device cpu --output /EVIDENCE/reviewed-binding.json
python scripts/check_atomic_nvalchemi.py --binding /EVIDENCE/reviewed-binding.json \
  --output-dir /EVIDENCE/NEW-native-gate
```

The first command is a trusted operator metadata probe, not qualification.
The second must complete every native and retention check without skips.
The [retained execution scope](../../docs/INTEGRATION_COMPLETION_20261018.md)
identifies the actual worker source revision and its equivalence to this
increment's unchanged atomic implementation. CUDA and physical gates remain open.
