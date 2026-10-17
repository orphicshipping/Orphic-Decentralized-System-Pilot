"""Explicitly bound CPU ALCHEMI worker using the existing adapter envelope.

No learned model, checkpoint fetch, remote service, or GPU is selected. Vendor
imports occur only after the complete request has passed the bounded contract.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile

from .nvalchemi_contract import (MODEL, OPERATION, UNITS, checked_input,
                                 configuration_id, validate_output)
from .protocol import AdapterRefusal
from .subprocess import _json

MAX_BYTES = 128 * 1024


def evaluate(request):
    request = checked_input(request)
    import torch
    import warp as wp

    # A fresh cache is scoped to this worker and removed on exit; never reuse
    # an unverified user cache as compiled provider code.
    wp.config.quiet = True
    wp.config.kernel_cache_dir = str(Path(os.environ["CIW_NVALCHEMI_CACHE"]))
    from nvalchemi.data import AtomicData, Batch
    from nvalchemi.models.lj import LennardJonesModelWrapper

    torch.set_num_threads(1)
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True)
    positions = torch.tensor(request["positions"], dtype=torch.float64, device="cpu")
    atoms = AtomicData(positions=positions,
                       atomic_numbers=torch.tensor(request["atomic_numbers"], dtype=torch.int64))
    batch = Batch.from_data_list([atoms])
    count = len(request["atom_ids"])
    # A complete directed neighbor matrix makes ordering and double-counting
    # explicit. The upstream hard-cutoff kernel handles pair distance. With
    # <=32 atoms this bounded all-pairs fixture needs no neighbor-list engine.
    batch["neighbor_matrix"] = torch.tensor(
        [[j for j in range(count) if j != i] for i in range(count)], dtype=torch.int32)
    batch["num_neighbors"] = torch.full((count,), count - 1, dtype=torch.int32)
    model = LennardJonesModelWrapper(epsilon=MODEL["epsilon_ev"], sigma=MODEL["sigma_angstrom"],
                                    cutoff=MODEL["cutoff_angstrom"], switch_width=0.0, half_list=False)
    output = model(batch)
    if tuple(output["energy"].shape) != (1, 1) or tuple(output["forces"].shape) != (count, 3):
        raise AdapterRefusal("MALFORMED_RESPONSE", "Vendor output shapes differ from the single-point contract")
    result = {"schema": "ciw.nvalchemi-lj-result.v1", "configuration_id": configuration_id(request),
              "atom_ids": list(request["atom_ids"]), "energy": output["energy"].item(),
              "forces": output["forces"].tolist(), "units": deepcopy(UNITS), "model": deepcopy(MODEL),
              "interpretation": "model_prediction",
              "energy_reference": "unshifted-pair-sum-zero-at-infinite-separation",
              "uncertainty": {"status": "unavailable"}}
    validate_output(result, request)
    return result


def main():
    response = {"schema": "ciw.adapter-response.v1", "status": "refused"}
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise AdapterRefusal("INPUT_LIMIT", "ALCHEMI worker request exceeds 128 KiB")
        request = _json(raw)
        if (not isinstance(request, dict) or set(request) != {"schema", "operation_id", "inputs"}
                or request["schema"] != "ciw.adapter-request.v1" or request["operation_id"] != OPERATION):
            raise AdapterRefusal("INVALID_INPUT", "Unsupported ALCHEMI worker request")
        with tempfile.TemporaryDirectory(prefix="ciw-nvalchemi-") as cache, redirect_stdout(sys.stderr):
            os.environ["CIW_NVALCHEMI_CACHE"] = cache
            result = evaluate(request["inputs"])
        response.update(status="ok", data=result)
    except AdapterRefusal as exc:
        response["refusal"] = exc.to_dict()
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        response["refusal"] = {"code": "INVALID_OUTPUT", "message": str(exc)}
    except Exception as exc:
        response["refusal"] = {"code": "RUNTIME_FAILED", "message": type(exc).__name__ + ": " + str(exc)}
    print(json.dumps(response, allow_nan=False))


if __name__ == "__main__":
    main()
