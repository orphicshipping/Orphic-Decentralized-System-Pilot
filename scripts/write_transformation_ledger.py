"""Write the transformation ledger and capability map. Running this is not a tool call."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from ciw.services import capability_map
    from ciw.transformation import document

    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    paths = [item.decode() for item in tracked.split(b"\0") if item]
    ledger = document(paths)
    (ROOT / "mcp" / "transformation-ledger.json").write_text(
        json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    (ROOT / "mcp" / "capability-map.json").write_text(
        json.dumps(capability_map(), indent=2) + "\n", encoding="utf-8")
    print(f"ledger {len(paths)} {ledger['counts']}")


if __name__ == "__main__":
    main()
