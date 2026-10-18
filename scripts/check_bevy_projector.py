"""Check the actual retained-render window in a dedicated software display.

Run under xvfb-run, with Pillow installed. The supplied record must contain a
trajectory: a window/summary alone cannot satisfy the visible-path gate.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import time


def file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--render", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    destination = args.output_dir.resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        parser.error("output directory must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)
    report = {"status": "FAIL", "native_window": True, "may_authorize": False}
    process = None
    log = None
    try:
        from PIL import ImageGrab, __version__ as pillow_version
        binary, source = args.binary.resolve(strict=True), args.render.resolve(strict=True)
        report.update(binary_sha256=file_digest(binary),
                      input_sha256=file_digest(source), pillow=pillow_version)
        summary = subprocess.run([str(binary), "--summary", str(source)],
                                 capture_output=True, check=True, timeout=15)
        data = json.loads(summary.stdout)
        if not data["source_available"] or data["may_authorize"] is not False or not any(len(p["points"]) >= 2 for p in data["paths"]):
            raise ValueError("visual qualification requires a retained path with at least two points")
        (destination / "summary.json").write_bytes(summary.stdout)
        (destination / "render-input.json").write_bytes(source.read_bytes())
        display = os.environ.get("DISPLAY")
        if not display:
            raise ValueError("run in a dedicated DISPLAY, for example using xvfb-run")
        log = (destination / "window.log").open("w")
        process = subprocess.Popen([str(binary), "--frames", "240", str(source)], stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline and process.poll() is None:
            time.sleep(.1)
            frame = ImageGrab.grab(xdisplay=display).convert("RGB")
            # Both declared path colors are distinct from the neutral text/background.
            pixels = sum(1 for red, green, blue in frame.getdata()
                         if green > 140 and red < 180 and green > red*1.2 and blue > red*1.2)
            if pixels > 25:
                image = destination / "projector.png"
                frame.save(image)
                report.update(visible_path_pixels=pixels, screenshot_size=list(frame.size),
                              screenshot_sha256=sha256(image.read_bytes()).hexdigest())
                break
        report["exit_code"] = process.wait(timeout=50)
        log.flush()
        text = (destination / "window.log").read_text()
        report["render_errors"] = [line for line in text.splitlines() if " ERROR " in line or "panicked at" in line]
        if (report["exit_code"] == 0 and "NCP_PROJECTOR_FRAMES_COMPLETE" in text
                and not report["render_errors"] and report.get("visible_path_pixels", 0) > 25):
            report["status"] = "PASS"
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if log is not None:
            log.close()
        (destination / "gate.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
