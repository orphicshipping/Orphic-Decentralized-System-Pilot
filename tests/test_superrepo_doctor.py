"""Preflight availability cannot stand in for execution or source qualification."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
HEAD, TREE, PIN = "1" * 40, "2" * 40, "3" * 40


@pytest.fixture
def doctor(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("superrepo_doctor_test", ROOT / "scripts/superrepo_doctor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for requirement in module.GATE_PREREQUISITES.values():
        (scripts / requirement["script"]).write_text("# A local gate fixture, never executed.\n")
    state = {"source_calls": 0, "dirty": False, "head": HEAD, "missing": set(), "disconnected": set()}

    def audit_source(root):
        state["source_calls"] += 1
        if state["dirty"]:
            raise ValueError("Changed tracked source")
        return {"revision": state["head"], "source_tree": TREE}

    def git(root, *arguments):
        revision = arguments[-1].split("^", 1)[0] if arguments[0] == "cat-file" else arguments[-2]
        if (arguments[0] == "cat-file" and revision in state["missing"]
                or arguments[0] == "merge-base" and revision in state["disconnected"]):
            raise subprocess.CalledProcessError(1, ["git", *arguments])
        return b""

    monkeypatch.setattr(module, "verify_terminal_source", audit_source)
    monkeypatch.setattr(module, "verify_imports", lambda root: {"provider": TREE})
    monkeypatch.setattr(module, "git", git)
    # Executable lookup must follow the actual host runtime, particularly the
    # Python 3.11/3.12 Windows shutil.which and _winapi differences.
    monkeypatch.setattr(module, "sys", SimpleNamespace(version_info=sys.version_info, executable=sys.executable))
    return module, root, state


def _checks(report, group):
    return {check["name"]: check for check in report["groups"][group]["checks"]}


def _verbose(name, version="1.90.0", host="x86_64-unknown-linux-gnu", commit=None):
    commit = commit or ("a" * 40 if name == "cargo" else "b" * 40)
    return (f"{name} {version} ({commit[:9]} 2025-09-14)\n"
            f"release: {version}\nhost: {host}\ncommit-hash: {commit}\n")


@pytest.fixture
def tools(doctor, monkeypatch, tmp_path):
    module, root, state = doctor
    # Tool-discovery tests assume an eligible lane without impersonating a
    # different interpreter. The untouched minimums are tested separately.
    monkeypatch.setattr(module, "GATE_PREREQUISITES", {
        name: {**requirement, "minimum_python": (3, 11)}
        for name, requirement in module.GATE_PREREQUISITES.items()
    })
    directory = tmp_path / "bin"
    directory.mkdir()
    for name in ("node", "npm", "uv", "cargo", "rustc", "rustdoc"):
        path = directory / name
        path.write_text("#!/usr/bin/env node\n" if name == "npm" else "#!/bin/sh\n")
        path.chmod(0o755)
    versions = {"node": "v24.19.0\n", "npm": "11.9.0\n", "uv": "uv 0.12.19 (x86_64-unknown-linux-gnu)\n",
                **{name: _verbose(name) for name in ("cargo", "rustc", "rustdoc")}}
    calls = []

    def discover(command, path=None):
        # These owned fixture files are never executed; version subprocesses
        # below are simulated. Discover their literal names on every platform
        # rather than requiring native launchers. Python 3.11's lookup helper
        # expands PATHEXT before calling which; map those candidates back to
        # our inert extensionless fixtures. Native Windows lookup tests below
        # restore real shutil.which and keep actual PATHEXT behavior.
        # File presence and selected PATH/directory ordering remain real.
        extensions = (module.os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").upper().split(module.os.pathsep)
        for directory_name in (path or "").split(module.os.pathsep):
            candidate = Path(directory_name) / command
            if module.os.name == "nt" and not candidate.is_file() and candidate.suffix.upper() in extensions:
                candidate = candidate.with_suffix("")
            if candidate.is_file() and module.os.access(candidate, module.os.X_OK):
                return str(candidate)
        return None

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[1:] in (["--version"], ["--version", "--verbose"])
        # Native wrapper fixtures can have .cmd/.exe suffixes; the simulated
        # version result still identifies the requested tool, not its wrapper.
        value = versions[Path(command[0]).stem]
        if Path(command[0]).stem == "npm":
            environment = {key.lower(): item for key, item in kwargs["env"].items()}
            user, global_config = (Path(environment[key]) for key in ("npm_config_userconfig", "npm_config_globalconfig"))
            assert user.resolve() != global_config.resolve()
            assert user.read_bytes() == global_config.read_bytes() == b""
        if isinstance(value, BaseException):
            raise value
        return SimpleNamespace(returncode=0, stdout=value, stderr="")

    monkeypatch.setenv("PATH", str(directory))
    for key in ("RUSTC", "RUSTDOC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(module, "shutil", SimpleNamespace(which=discover))
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=run, PIPE=subprocess.PIPE,
                                                             SubprocessError=subprocess.SubprocessError))
    return directory, versions, calls


def test_available_is_only_preflight_with_fresh_occurrence_and_stable_source(doctor):
    module, root, state = doctor
    first = module.inspect_workspace(root=root, groups=["measurement", "measurement"])
    second = module.inspect_workspace(root=root, groups=["measurement"])
    assert first["status"] == second["status"] == "available"
    assert list(first["groups"]) == ["measurement"]
    assert first["preflight_id"] != second["preflight_id"]
    assert first["terminal_source"] == second["terminal_source"] == {"revision": HEAD, "source_tree": TREE}
    assert first["qualification"] == first["groups"]["measurement"]["qualification"] == "not_performed"
    assert first["dependencies"]["installation"] == "not_performed"
    assert first["network"] == "not_attempted"
    assert first["admission"] == "not_performed"
    assert first["independent_verification"] is False
    assert "verification_id" not in first
    assert state["source_calls"] == 4


@pytest.mark.parametrize("version,inference_status", [((3, 11, 9), "missing"), ((3, 12, 14), "available")])
def test_python_minimum_is_lane_specific_and_distinct_from_configured_ci(doctor, monkeypatch, version, inference_status):
    module, root, _ = doctor
    module.sys.version_info = version
    # This test varies lane eligibility only, never the host OS lookup API.
    monkeypatch.setattr(module, "_tool", lambda name, *args: {"name": name, "status": "missing"})
    result = module.inspect_workspace(root=root, groups=["measurement", "inference", "surface", "operations", "web"])
    assert _checks(result, "measurement")["python"]["status"] == "available"
    for group in ("inference", "operations", "web"):
        assert _checks(result, group)["python"]["status"] == inference_status
    assert _checks(result, "surface")["python"]["status"] == "available"
    assert result["groups"]["surface"]["configured_ci_python"] == ["3.12", "3.13"]
    assert result["groups"]["surface"]["qualification"] == "not_performed"


def test_refused_source_is_not_used_to_probe_toolchains(doctor, tools):
    module, root, state = doctor
    _, _, calls = tools
    state["dirty"] = True
    result = module.inspect_workspace(root=root, groups=["web", "operations"])
    assert result["status"] == result["source_audit"]["status"] == "refused"
    assert calls == []
    assert "terminal_source" not in result


@pytest.mark.parametrize("change", ["dirty", "head"])
def test_source_change_during_version_probe_refuses_preflight(doctor, tools, monkeypatch, change):
    module, root, state = doctor
    directory, _, _ = tools
    original = module.subprocess.run

    def run(*args, **kwargs):
        state[change] = True if change == "dirty" else "4" * 40
        return original(*args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", run)
    report = module.inspect_workspace(root=root, groups=["web"], node_bin=directory)
    assert report["source_audit"]["status"] == report["status"] == "refused"
    assert report["qualification"] == "not_performed"


def test_available_cargo_does_not_substitute_for_missing_rustdoc(doctor, tools):
    module, root, _ = doctor
    directory, _, _ = tools
    (directory / "rustdoc").unlink()
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=directory / "cargo")
    checks = _checks(report, "operations")
    assert checks["cargo"]["status"] == checks["rustc"]["status"] == "available"
    assert checks["rustdoc"]["status"] == checks["rust_toolchain_context"]["status"] == "missing"
    assert report["status"] == "missing"


def test_explicit_cargo_outside_path_forwards_the_actual_compiler_and_rustdoc_context(doctor, tools, monkeypatch, tmp_path):
    module, root, _ = doctor
    directory, _, calls = tools
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=directory / "cargo")
    checks = _checks(report, "operations")
    assert checks["cargo"]["status"] == "available"
    assert checks["rustc"]["status"] == checks["rustdoc"]["status"] == "available"
    assert checks["rust_toolchain_context"]["status"] == "available"
    assert len(calls) == 3
    assert all(kwargs["env"]["PATH"].split(module.os.pathsep)[0] == str(directory) for _, kwargs in calls)


def test_explicit_cargo_proxy_preserves_invocation_name_bin_context_and_resolved_identity(doctor, tools, monkeypatch, tmp_path):
    module, root, _ = doctor
    directory, versions, calls = tools
    proxy_directory = tmp_path / "proxy"
    proxy_directory.mkdir()
    proxy = proxy_directory / "rustup"
    proxy.write_text("#!/bin/sh\n")
    proxy.chmod(0o755)
    versions["rustup"] = "rustup 1.28.0\n"
    cargo = directory / "cargo"
    cargo.unlink()
    try:
        cargo.symlink_to(proxy)
    except OSError:
        # Native Windows runners may lack symlink privilege. Model just the
        # proxy's resolved identity while retaining real executable selection
        # and lexical invocation assertions; no platform coverage is skipped.
        cargo.write_text("#!/bin/sh\n")
        cargo.chmod(0o755)
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            return proxy if path.absolute() == cargo else original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve)
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=cargo)
    selected = _checks(report, "operations")["cargo"]
    assert report["status"] == "available"
    assert selected["entrypoint"] == str(cargo)
    assert selected["executable"] == str(proxy)
    assert calls[0][0][0] == str(cargo)
    assert all(kwargs["env"]["PATH"].split(module.os.pathsep)[0] == str(directory) for _, kwargs in calls)


def test_probe_disables_rustup_auto_install_without_changing_toolchain_selection_or_check_environment(doctor, tools, monkeypatch):
    module, root, _ = doctor
    directory, _, calls = tools
    monkeypatch.setenv("RUSTUP_AUTO_INSTALL", "1")
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", "selected-existing-toolchain")
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=directory / "cargo")
    assert report["status"] == "available"
    for _, kwargs in calls:
        assert kwargs["env"]["RUSTUP_AUTO_INSTALL"] == "0"
        assert kwargs["env"]["RUSTUP_TOOLCHAIN"] == "selected-existing-toolchain"
    assert module.configured_environment(cargo=directory / "cargo")["RUSTUP_AUTO_INSTALL"] == "1"
    assert module.os.environ["RUSTUP_AUTO_INSTALL"] == "1"


def test_missing_rustup_toolchain_remains_refused_without_auto_install(doctor, tools, monkeypatch):
    module, root, _ = doctor
    directory, _, _ = tools
    monkeypatch.setenv("RUSTUP_AUTO_INSTALL", "1")
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", "selected-absent-toolchain")

    def absent_toolchain(command, **kwargs):
        assert kwargs["env"]["RUSTUP_AUTO_INSTALL"] == "0"
        assert kwargs["env"]["RUSTUP_TOOLCHAIN"] == "selected-absent-toolchain"
        return SimpleNamespace(returncode=1, stdout="", stderr="toolchain is not installed")

    monkeypatch.setattr(module.subprocess, "run", absent_toolchain)
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=directory / "cargo")
    assert report["status"] == "refused"
    assert _checks(report, "operations")["cargo"]["exit_code"] == 1
    assert report["dependencies"]["installation"] == "not_performed"
    assert report["qualification"] == "not_performed"


def test_shared_toolchain_environment_has_stable_order_and_preserves_explicit_compilers(doctor, tools, monkeypatch, tmp_path):
    module, _, _ = doctor
    directory, _, _ = tools
    node_bin = tmp_path / "node-bin"
    node_bin.mkdir()
    monkeypatch.setenv("RUSTC", "/selected/compiler")
    monkeypatch.setenv("RUSTDOC", "/selected/documentation")
    monkeypatch.setenv("PYTHONPATH", "/do-not-import")
    monkeypatch.setenv("GIT_INDEX_FILE", "/do-not-use-index")
    inherited = dict(module.os.environ)
    environment = module.configured_environment(cargo=directory / "cargo", node_bin=node_bin)
    assert environment["PATH"].split(module.os.pathsep)[:2] == [str(node_bin), str(directory)]
    assert environment["RUSTC"] == "/selected/compiler"
    assert environment["RUSTDOC"] == "/selected/documentation"
    assert "PYTHONPATH" not in environment and "GIT_INDEX_FILE" not in environment
    assert dict(module.os.environ) == inherited


def test_explicit_compiler_and_rustdoc_environment_identifies_matching_context(doctor, tools, monkeypatch, tmp_path):
    module, root, _ = doctor
    directory, _, _ = tools
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("RUSTC", str(directory / "rustc"))
    monkeypatch.setenv("RUSTDOC", str(directory / "rustdoc"))
    report = module.inspect_workspace(root=root, groups=["operations"], cargo=directory / "cargo")
    context = _checks(report, "operations")["rust_toolchain_context"]
    assert report["status"] == context["status"] == "available"
    assert context["configured_ci_pin_match"] is True
    assert context["compilation"] == "not_performed"


@pytest.mark.parametrize("field,value", [("version", "1.91.0"), ("host", "aarch64-unknown-linux-gnu"), ("commit", "c" * 40)])
def test_different_compiler_and_rustdoc_contexts_are_refused(doctor, tools, field, value):
    module, root, _ = doctor
    _, versions, _ = tools
    versions["rustdoc"] = _verbose("rustdoc", **{field: value})
    report = module.inspect_workspace(root=root, groups=["operations"])
    assert _checks(report, "operations")["rust_toolchain_context"]["status"] == report["status"] == "refused"


def test_new_matching_rust_version_records_ci_difference_without_arbitrary_refusal(doctor, tools):
    module, root, _ = doctor
    _, versions, _ = tools
    versions.update({name: _verbose(name, "1.91.0") for name in ("cargo", "rustc", "rustdoc")})
    report = module.inspect_workspace(root=root, groups=["operations"])
    context = _checks(report, "operations")["rust_toolchain_context"]
    assert context["status"] == "available"
    assert context["configured_ci_pin"] == "1.90.0"
    assert context["configured_ci_pin_match"] is False
    assert report["qualification"] == "not_performed"


def test_compiler_wrapper_is_explicitly_uninspected(doctor, tools, monkeypatch):
    module, root, _ = doctor
    monkeypatch.setenv("RUSTC_WRAPPER", "do-not-execute-wrapper")
    report = module.inspect_workspace(root=root, groups=["operations"])
    assert _checks(report, "operations")["rust_wrapper_context"]["status"] == "refused"


def test_selected_node_pair_is_available_without_provider_credentials_or_npm_auth(doctor, tools, monkeypatch):
    module, root, _ = doctor
    directory, _, calls = tools
    monkeypatch.setenv("CIW_PROVIDER_READ_TOKEN", "do-not-retain-secret")
    monkeypatch.setenv("npm_config_registry", "https://do-not-contact.invalid")
    monkeypatch.setenv("NODE_OPTIONS", "--require do-not-load")
    monkeypatch.setenv("NODE_PATH", "/selected/node/modules")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/selected/libraries")
    monkeypatch.setenv("LD_PRELOAD", "/selected/loader.so")
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", "selected-rust-toolchain")
    monkeypatch.setenv("PYTHONPATH", "do-not-import")
    report = module.inspect_workspace(root=root, groups=["web"], node_bin=directory)
    assert report["status"] == "available"
    assert _checks(report, "web")["node_npm_context"]["status"] == "available"
    for command, kwargs in calls:
        # Windows normalizes os.environ keys to uppercase; the child context
        # has the same values regardless of that platform casing convention.
        environment = {key.upper(): value for key, value in kwargs["env"].items()}
        if Path(command[0]).stem == "npm":
            user = Path(environment["NPM_CONFIG_USERCONFIG"])
            global_config = Path(environment["NPM_CONFIG_GLOBALCONFIG"])
            assert user != global_config
            assert not user.exists() and not global_config.exists()
        else:
            assert environment["NPM_CONFIG_USERCONFIG"] == module.os.devnull
        assert not {"CIW_PROVIDER_READ_TOKEN", "PYTHONPATH"} & environment.keys()
        assert environment["NODE_OPTIONS"] == "--require do-not-load"
        assert environment["NODE_PATH"] == "/selected/node/modules"
        assert environment["LD_LIBRARY_PATH"] == "/selected/libraries"
        assert environment["LD_PRELOAD"] == "/selected/loader.so"
        assert environment["RUSTUP_TOOLCHAIN"] == "selected-rust-toolchain"
        assert environment["NPM_CONFIG_REGISTRY"] == "https://do-not-contact.invalid"
    assert "do-not-retain-secret" not in json.dumps(report)


def test_installed_posix_npm_version_uses_distinct_empty_configs_and_cleans_them(doctor, monkeypatch):
    module, root, _ = doctor
    if os.name != "posix":
        pytest.skip("Real POSIX npm version regression; opaque native Windows launchers are tested separately")
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("Installed npm is optional for this real-tool regression")
    observed = []
    actual_run = subprocess.run

    def run(command, **kwargs):
        environment = {key.lower(): value for key, value in kwargs["env"].items()}
        configs = tuple(Path(environment[key]) for key in ("npm_config_userconfig", "npm_config_globalconfig"))
        assert configs[0].resolve() != configs[1].resolve()
        assert all(path.read_bytes() == b"" for path in configs)
        observed.extend(configs)
        return actual_run(command, **kwargs)

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=run, PIPE=subprocess.PIPE,
                                                             SubprocessError=subprocess.SubprocessError))
    source_files = set(root.rglob("*"))
    result = module._tool("npm", root, module._probe_environment(), Path(npm))
    assert result["status"] == "available", result
    assert result["version"]
    assert len(observed) == 2
    assert all(not path.parent.exists() for path in observed)
    assert set(root.rglob("*")) == source_files


def test_incomplete_explicit_node_bin_does_not_probe_fallback_npm(doctor, tools, tmp_path):
    module, root, _ = doctor
    _, _, calls = tools
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    node = chosen / "node"
    node.write_text("#!/bin/sh\n")
    node.chmod(0o755)
    report = module.inspect_workspace(root=root, groups=["web"], node_bin=chosen)
    assert _checks(report, "web")["npm"]["status"] == "missing"
    assert len(calls) == 1
    assert Path(calls[0][0][0]) == node


@pytest.mark.parametrize("filename,launcher", [("npm.cmd", "@ECHO OFF\n"), ("npm.exe", "opaque-native-launcher\n")])
def test_opaque_native_npm_launcher_is_refused_even_when_its_version_probe_succeeds(doctor, tools, monkeypatch, filename, launcher):
    module, root, _ = doctor
    directory, _, _ = tools
    original = module.shutil.which
    native = directory / filename
    native.write_text(launcher)
    native.chmod(0o755)

    def discover(command, path=None):
        return str(native) if Path(command).stem == "npm" else original(command, path=path)

    monkeypatch.setattr(module.shutil, "which", discover)
    report = module.inspect_workspace(root=root, groups=["web"], node_bin=directory)
    checks = _checks(report, "web")
    assert checks["npm"]["status"] == "available"
    assert checks["node_npm_context"]["status"] == report["status"] == "refused"
    assert "launcher" in checks["node_npm_context"]["reason"]
    assert report["qualification"] == "not_performed"


@pytest.mark.parametrize("problem", ["old_node", "different_npm_node", "absent_directory"])
def test_unavailable_node_context_is_not_reported_ready(doctor, tools, problem):
    module, root, _ = doctor
    directory, versions, calls = tools
    if problem == "old_node":
        versions["node"] = "v22.0.0\n"
    elif problem == "different_npm_node":
        (directory / "npm").write_text("#!/another/node\n")
    else:
        directory = directory / "absent"
    report = module.inspect_workspace(root=root, groups=["web"], node_bin=directory)
    assert report["status"] in {"missing", "refused"}
    if problem == "absent_directory":
        assert calls == []


def test_surface_requires_uv_without_installing_its_dependencies(doctor, tools):
    module, root, _ = doctor
    directory, _, calls = tools
    (directory / "uv").unlink()
    report = module.inspect_workspace(root=root, groups=["surface"])
    assert _checks(report, "surface")["uv"]["status"] == report["status"] == "missing"
    assert calls == []
    assert report["dependencies"]["status"] == "not_checked"


@pytest.mark.parametrize("output", ["", "pretend-tool 1.2.3", "uv unknown-version"])
def test_unidentified_version_output_is_refused(doctor, tools, output):
    module, root, _ = doctor
    _, versions, _ = tools
    versions["uv"] = output
    report = module.inspect_workspace(root=root, groups=["surface"])
    assert _checks(report, "surface")["uv"]["status"] == "refused"


def test_version_timeout_is_retained_as_refusal(doctor, tools):
    module, root, _ = doctor
    _, versions, _ = tools
    versions["uv"] = subprocess.TimeoutExpired(["uv", "--version"], 10)
    report = module.inspect_workspace(root=root, groups=["surface"])
    assert _checks(report, "surface")["uv"]["error_type"] == "TimeoutExpired"
    assert report["status"] == "refused"


@pytest.mark.parametrize("state_key,status", [("missing", "missing"), ("disconnected", "refused")])
def test_exact_helper_history_must_be_present_and_reachable(doctor, state_key, status):
    module, root, state = doctor
    helper = root / "scripts/check_monorepo.py"
    helper.write_text(f"SET_REVISION = '{PIN}'\n")
    state[state_key].add(PIN)
    report = module.inspect_workspace(root=root, groups=["measurement"])
    history = _checks(report, "measurement")["history:check_monorepo.py:SET_REVISION"]
    assert history["revision"] == PIN
    assert history["status"] == report["status"] == status
    assert history["network"] == "not_attempted"


def test_helpers_are_parsed_recursively_without_executing_their_top_level_code(doctor):
    module, root, _ = doctor
    marker = root / "do-not-create"
    (root / "scripts/check_monorepo.py").write_text(
        f"from check_monorepo_other import helper\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
    (root / "scripts/check_monorepo_other.py").write_text(f"OTHER_REVISION = '{PIN}'\n")
    report = module.inspect_workspace(root=root, groups=["measurement"])
    assert report["status"] == "available"
    assert "helper:check_monorepo_other.py" in _checks(report, "measurement")
    assert not marker.exists()


@pytest.mark.parametrize("problem,status", [("missing", "missing"), ("syntax", "refused"), ("symlink", "refused")])
def test_gate_helper_input_problems_are_visible(doctor, problem, status):
    module, root, _ = doctor
    helper = root / "scripts/check_monorepo.py"
    if problem == "missing":
        helper.unlink()
    elif problem == "syntax":
        helper.write_text("def broken(:\n")
    else:
        original = root / "original.py"
        helper.rename(original)
        helper.symlink_to(original)
    report = module.inspect_workspace(root=root, groups=["measurement"])
    assert _checks(report, "measurement")["helper:check_monorepo.py"]["status"] == report["status"] == status


def test_cli_json_is_reviewable_and_nonqualifying(doctor, capsys):
    module, root, _ = doctor
    assert module.main(["--root", str(root), "--group", "measurement", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "notations.superrepo-preflight.v1"
    assert report["status"] == "available"
    assert report["qualification"] == "not_performed"
    assert not list(root.rglob("report.json"))


def test_unknown_group_is_rejected_before_any_source_or_tool_probe(doctor):
    module, root, state = doctor
    with pytest.raises(ValueError, match="registered"):
        module.inspect_workspace(root=root, groups=["unexpected"])
    assert state["source_calls"] == 0


def test_invalid_version_encoding_is_a_retained_refusal(doctor, tools):
    module, root, _ = doctor
    _, versions, _ = tools
    versions["uv"] = UnicodeDecodeError("utf-8", bytes([255]), 0, 1, "invalid start byte")
    report = module.inspect_workspace(root=root, groups=["surface"])
    check = _checks(report, "surface")["uv"]
    assert report["status"] == check["status"] == "refused"
    assert check["error_type"] == "UnicodeDecodeError"
    assert report["qualification"] == "not_performed"


def test_relative_tool_search_is_not_misrepresented_across_gate_directories(doctor, tools, monkeypatch):
    module, root, _ = doctor
    directory, _, calls = tools
    child_bin = root / "bin"
    child_bin.mkdir()
    expected = child_bin / "uv"
    expected.write_text("#!/bin/sh" + chr(10))
    expected.chmod(0o755)
    # The caller and child each have a different bin/uv; only the latter is
    # available to the gate executing with cwd=root and PATH=bin.
    monkeypatch.chdir(directory.parent)
    monkeypatch.setenv("PATH", "bin")
    report = module.inspect_workspace(root=root, groups=["surface"])
    selected = _checks(report, "surface")["uv"]
    if os.name != "nt":
        assert report["status"] == selected["status"] == "refused"
        assert "working directory" in selected["reason"]
        assert calls == []
    else:
        assert report["status"] == "available"
        assert selected["entrypoint"] == str(expected)
        assert calls[0][0][0] == str(expected)
        assert calls[0][1]["cwd"] == root
        assert calls[0][1]["env"]["PATH"] == "bin"


@pytest.mark.skipif(os.name != "nt", reason="Windows implicit executable search semantics")
@pytest.mark.parametrize("suppress_current_directory", [False, True])
def test_windows_real_lookup_does_not_probe_a_caller_directory_tool(doctor, tools, monkeypatch, suppress_current_directory):
    module, root, _ = doctor
    directory, _, calls = tools
    # These files are never executed: only actual shutil.which discovery is
    # used; the existing version subprocess fixture remains mocked.
    for path in (directory.parent / "uv.exe", root / "uv.exe", directory / "uv.exe"):
        path.write_bytes(b"owned lookup fixture")
    monkeypatch.chdir(directory.parent)
    monkeypatch.setenv("PATH", str(directory))
    monkeypatch.setenv("PATHEXT", ".EXE")
    if suppress_current_directory:
        monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    else:
        monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    monkeypatch.setattr(module, "shutil", shutil)
    with monkeypatch.context() as child_context:
        child_context.chdir(root)
        expected = Path(shutil.which("uv", path=str(directory))).absolute()
    report = module.inspect_workspace(root=root, groups=["surface"])
    selected = _checks(report, "surface")["uv"]
    assert report["status"] == "available"
    assert Path(selected["entrypoint"]) == expected
    assert Path(calls[0][0][0]) == expected


@pytest.mark.parametrize("compiler", ["rustc", "rustdoc"])
def test_relative_compiler_environment_is_refused_without_probe(doctor, tools, monkeypatch, compiler):
    module, root, _ = doctor
    directory, _, calls = tools
    child_bin = root / "bin"
    child_bin.mkdir()
    candidate = child_bin / compiler
    candidate.write_text("#!/bin/sh" + chr(10))
    candidate.chmod(0o755)
    monkeypatch.chdir(directory.parent)
    monkeypatch.setenv(compiler.upper(), "bin/" + compiler)
    report = module.inspect_workspace(root=root, groups=["operations"])
    assert report["status"] == "refused"
    selected = _checks(report, "operations")[compiler]
    assert selected["status"] == "refused"
    assert "relative" in selected["reason"].lower()
    assert "absolute" in selected["reason"].lower()
    assert "entrypoint" not in selected
    assert all(Path(command[0]).stem != compiler for command, _ in calls)
    assert _checks(report, "operations")["rust_toolchain_context"]["status"] == "refused"


@pytest.mark.parametrize("compiler", ["rustc", "rustdoc"])
@pytest.mark.parametrize("entry", ["bin", ""])
def test_selected_relative_compiler_path_is_refused_without_probe(doctor, tools, monkeypatch, compiler, entry):
    module, root, _ = doctor
    directory, _, calls = tools
    selected_bin = root / entry
    selected_bin.mkdir(exist_ok=True)
    candidate = selected_bin / compiler
    candidate.write_text("#!/bin/sh" + chr(10))
    candidate.chmod(0o755)
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    selected = module._tool(compiler, root, {"PATH": entry + os.pathsep + str(directory)})
    assert selected["status"] == "refused"
    assert "working directory" in selected["reason"]
    assert calls == []


@pytest.mark.parametrize("compiler", ["rustc", "rustdoc"])
def test_unused_relative_path_does_not_refuse_absolute_compiler_selection(doctor, tools, monkeypatch, compiler):
    module, root, _ = doctor
    directory, _, calls = tools
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    selected = module._tool(compiler, root, {"PATH": "missing" + os.pathsep + str(directory)})
    assert selected["status"] == "available"
    assert Path(selected["entrypoint"]) == directory / compiler
    assert len(calls) == 1


@pytest.mark.parametrize("compiler", ["rustc", "rustdoc"])
def test_absolute_compiler_environment_binding_survives_relative_path(doctor, tools, compiler):
    module, root, _ = doctor
    directory, _, calls = tools
    selected = module._tool(compiler, root, {"PATH": "bin"}, supplied=str(directory / compiler))
    assert selected["status"] == "available"
    assert Path(selected["entrypoint"]) == directory / compiler
    assert len(calls) == 1


@pytest.mark.parametrize("name", ["cargo", "uv"])
def test_posix_selected_relative_launch_is_refused_without_probe(doctor, tools, monkeypatch, name):
    module, root, _ = doctor
    _, _, calls = tools
    selected_bin = root / "bin"
    selected_bin.mkdir()
    candidate = selected_bin / name
    candidate.write_text("#!/bin/sh" + chr(10))
    candidate.chmod(0o755)
    # Exercise the POSIX launch branch with inert files on every host.
    # This is inspect-only branch coverage, not a POSIX subprocess qualification.
    monkeypatch.setattr(module, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))
    selected = module._tool(name, root, {"PATH": "bin"})
    assert selected["status"] == "refused"
    assert "working directory" in selected["reason"]
    assert calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows implicit compiler lookup semantics")
@pytest.mark.parametrize("compiler", ["rustc", "rustdoc"])
def test_windows_implicit_compiler_directory_is_refused_without_probe(doctor, tools, monkeypatch, compiler):
    module, root, _ = doctor
    directory, _, calls = tools
    for candidate in (root / (compiler + ".exe"), directory / (compiler + ".exe")):
        candidate.write_bytes(b"owned lookup fixture")
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    monkeypatch.setattr(module, "shutil", shutil)
    selected = module._tool(compiler, root, {"PATH": str(directory)})
    assert selected["status"] == "refused"
    assert "working directory" in selected["reason"]
    assert calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows PATHEXT and implicit lookup semantics")
def test_windows_selected_node_directory_ignores_caller_shadowing(doctor, tools, monkeypatch):
    module, root, _ = doctor
    directory, _, calls = tools
    for path in (directory.parent / "node.exe", directory.parent / "npm.cmd",
                 directory / "node.exe", directory / "npm.cmd"):
        path.write_bytes(b"owned lookup fixture")
    monkeypatch.chdir(directory.parent)
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    monkeypatch.setattr(module, "shutil", shutil)
    report = module.inspect_workspace(root=root, groups=["web"], node_bin=directory)
    checks = _checks(report, "web")
    assert Path(checks["node"]["entrypoint"]) == directory / "node.exe"
    assert Path(checks["npm"]["entrypoint"]) == directory / "npm.cmd"
    assert [Path(command[0]).parent for command, _ in calls] == [directory, directory]
    # Native npm is still deliberately unqualified by this launcher policy.
    assert checks["node_npm_context"]["status"] == report["status"] == "refused"


@pytest.mark.skipif(os.name != "nt", reason="Python 3.11 Windows PATHEXT compatibility")
def test_python311_windows_lookup_keeps_pathext_on_absolute_candidates(doctor, monkeypatch):
    module, root, _ = doctor
    selected = root / "tools"
    selected.mkdir()
    tool = selected / "uv.EXE"
    tool.write_bytes(b"owned lookup fixture")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setattr(module, "shutil", shutil)
    module.sys.version_info = (3, 11, 9)
    assert Path(module._which_at_root("uv", root, str(selected))) == tool
