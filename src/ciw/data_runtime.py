"""Data-consumer runtime metadata without importing executable providers."""
from hashlib import sha256
from pathlib import Path
import platform


def comparison_identity():
    return {"provider": "ciw.control-checks", "version": "1",
            "profile": "data.compare-observations.v1"}


def polymer_identity():
    root = Path(__file__).parent
    names = ("polymer_contract.py", "polymer_metrology.py", "polymer_models.py",
             "polymer_copilot.py", "polymer_verification.py", "polymer_ingress.py", "polymer_workflow.py")
    raw = b"\0".join(name.encode() + b"\0" +
                     (root / name).read_text(encoding="utf-8").replace("\r\n", "\n").encode()
                     for name in names)
    return {"provider": "ciw.polymer.assessment", "version": "1", "code_sha256": sha256(raw).hexdigest(),
            "source_normalization": "utf8_lf", "scope": "retained_cycle_reference_models_and_simulated_control_only",
            "environment": {"python": platform.python_version(), "floating_point": "binary64"},
            "bridge_code_sha256": sha256((root / "data_compute_bridge.py").read_bytes()).hexdigest(),
            "input_seam": "retained_native_data_source"}


def product_study_identity():
    root = Path(__file__).parent
    names = ("product_study.py", "product_study_workflow.py", "data_runtime.py")
    raw = b"\0".join(name.encode() + b"\0" + (root / name).read_text(encoding="utf-8").replace("\r\n", "\n").encode() for name in names)
    return {"provider": "ciw.product-study-review", "version": "1", "code_sha256": sha256(raw).hexdigest(),
            "scope": "selected_artifact_binding_and_scalar_screens_only", "source_normalization": "utf8_lf"}
