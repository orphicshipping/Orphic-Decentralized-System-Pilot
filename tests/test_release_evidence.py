"""Adversarial tests for immutable-candidate release evidence.

Use only the standard library. Every network-facing test supplies a fake API,
and command tests write exclusively inside TemporaryDirectory.
"""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "net_release_evidence_under_test", ROOT / "scripts/check_release_evidence.py"
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

SHA = "a" * 40
OTHER_SHA = "b" * 40
REPOSITORY = "example/engineering"
WORKFLOW = ".github/workflows/release.yml"


def policy_fixture():
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "required_workflows": [{
            "path": WORKFLOW,
            "jobs": ["build", "replay"],
            "allowed_skipped_jobs": ["slow-reproduction"],
            "excluded_jobs": {},
            "artifacts": ["release-evidence"],
        }],
    }


def run_fixture():
    return {
        "id": 101,
        "run_attempt": 2,
        "head_sha": SHA,
        "head_repository": {"full_name": REPOSITORY},
        "path": WORKFLOW,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "run_started_at": "2026-10-03T10:00:00Z",
        "updated_at": "2026-10-03T10:10:00Z",
        "html_url": "https://github.com/example/engineering/actions/runs/101",
    }


def evidence_fixture():
    run = run_fixture()
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "candidate_sha": SHA,
        "collected_at": "2026-10-03T10:11:00+00:00",
        "workflows": [{
            "path": WORKFLOW,
            "run": run,
            "jobs": [
                {
                    "id": 201 + index,
                    "name": name,
                    "run_id": run["id"],
                    "run_attempt": run["run_attempt"],
                    "head_sha": SHA,
                    "status": "completed",
                    "conclusion": "success",
                }
                for index, name in enumerate(["build", "replay", "slow-reproduction"])
            ],
            "artifacts": [{
                "id": 301,
                "name": "release-evidence",
                "expired": False,
                "size_in_bytes": 1024,
                "created_at": "2026-10-03T10:05:00Z",
                "workflow_run": {"id": run["id"], "head_sha": SHA},
            }],
        }],
    }


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.policy = policy_fixture()
        self.evidence = evidence_fixture()

    @property
    def record(self):
        return self.evidence["workflows"][0]

    @property
    def requirement(self):
        return self.policy["required_workflows"][0]

    def report(self):
        return gate.evaluate(self.policy, self.evidence, SHA)

    def assert_blocked(self):
        report = self.report()
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["workflows"][0]["reasons"])
        return report

    def assert_refused(self):
        try:
            report = self.report()
        except (ValueError, TypeError, KeyError):
            return
        self.assertEqual(report["status"], "blocked")

    def test_complete_exact_candidate_passes_and_binds_policy(self):
        report = self.report()
        self.assertEqual(report["status"], "ci_pass")
        self.assertEqual(report["candidate_sha"], SHA)
        self.assertEqual(report["repository"], REPOSITORY)
        self.assertEqual(len(report["policy_sha256"]), 64)
        changed = deepcopy(self.policy)
        changed["required_workflows"][0]["excluded_jobs"] = {
            "private-provider": "Separate private-provider qualification."
        }
        other = gate.evaluate(changed, self.evidence, SHA)
        self.assertNotEqual(report["policy_sha256"], other["policy_sha256"])

    def test_push_and_manual_are_the_only_accepted_events(self):
        for event in ("push", "workflow_dispatch"):
            with self.subTest(event=event):
                self.record["run"]["event"] = event
                self.assertEqual(self.report()["status"], "ci_pass")
        for event in ("pull_request", "pull_request_target", "schedule", "workflow_call", None):
            with self.subTest(event=event):
                self.record["run"]["event"] = event
                self.assert_blocked()

    def test_evidence_envelope_must_match_repository_candidate_and_schema(self):
        for field, value in (
            ("repository", "different/engineering"),
            ("candidate_sha", OTHER_SHA),
            ("candidate_sha", SHA[:8]),
            ("schema_version", 2),
            ("schema_version", True),
        ):
            with self.subTest(field=field, value=value):
                self.evidence = evidence_fixture()
                self.evidence[field] = value
                self.assert_refused()

    def test_run_requires_exact_sha_repository_path_and_integer_identity(self):
        for field, value in (
            ("head_sha", OTHER_SHA),
            ("path", ".github/workflows/other.yml"),
            ("path", WORKFLOW + "@refs/heads/main"),
            ("head_repository", {"full_name": "fork/engineering"}),
            ("head_repository", None),
            ("id", 0),
            ("id", True),
            ("run_attempt", 0),
            ("run_attempt", True),
        ):
            with self.subTest(field=field, value=value):
                self.evidence = evidence_fixture()
                self.record["run"][field] = value
                self.assert_blocked()

    def test_missing_workflow_and_run_are_blocked(self):
        self.evidence["workflows"] = []
        self.assert_blocked()
        self.evidence = evidence_fixture()
        for value in (None, [], "missing"):
            with self.subTest(value=value):
                self.record["run"] = value
                self.assert_blocked()

    def test_queued_failed_cancelled_and_skipped_runs_cannot_pass(self):
        for status, conclusion in (
            ("queued", None), ("in_progress", None), ("completed", "failure"),
            ("completed", "cancelled"), ("completed", "skipped"),
            ("completed", "neutral"), ("completed", "timed_out"),
        ):
            with self.subTest(status=status, conclusion=conclusion):
                self.evidence = evidence_fixture()
                self.record["run"].update(status=status, conclusion=conclusion)
                self.assert_blocked()

    def test_missing_or_renamed_required_job_is_blocked(self):
        self.record["jobs"] = self.record["jobs"][1:]
        self.assert_blocked()
        self.evidence = evidence_fixture()
        self.record["jobs"][0]["name"] = "build (different matrix)"
        self.assert_blocked()

    def test_every_required_job_needs_completed_success(self):
        for status, conclusion in (
            ("queued", None), ("in_progress", None), ("completed", "failure"),
            ("completed", "cancelled"), ("completed", "skipped"),
            ("completed", "neutral"), ("completed", "timed_out"),
        ):
            with self.subTest(status=status, conclusion=conclusion):
                self.evidence = evidence_fixture()
                self.record["jobs"][0].update(status=status, conclusion=conclusion)
                self.assert_blocked()

    def test_only_explicit_optional_job_can_skip(self):
        optional = self.record["jobs"][-1]
        optional["conclusion"] = "skipped"
        self.assertEqual(self.report()["status"], "ci_pass")
        for status, conclusion in (
            ("queued", None), ("completed", "failure"), ("completed", "cancelled")
        ):
            with self.subTest(status=status, conclusion=conclusion):
                optional.update(status=status, conclusion=conclusion)
                self.assert_blocked()
        self.evidence = evidence_fixture()
        self.record["jobs"].pop()
        self.assert_blocked()

    def test_unlisted_job_failure_is_not_silently_ignored(self):
        extra = deepcopy(self.record["jobs"][0])
        extra.update(id=299, name="new-public-lane", conclusion="failure")
        self.record["jobs"].append(extra)
        self.assert_blocked()

    def test_excluded_failure_is_scoped_and_reported(self):
        self.requirement["excluded_jobs"] = {"private-provider": "Needs private access."}
        private = deepcopy(self.record["jobs"][0])
        private.update(id=299, name="private-provider", conclusion="failure")
        self.record["jobs"].append(private)
        self.record["run"]["conclusion"] = "failure"
        report = self.report()
        self.assertEqual(report["status"], "ci_pass")
        self.assertEqual(
            report["workflows"][0]["excluded_jobs"],
            {"private-provider": "Needs private access."},
        )
        self.record["jobs"][0]["conclusion"] = "failure"
        self.assert_blocked()

    def test_unexplained_workflow_failure_remains_blocked_with_exclusions(self):
        self.requirement["excluded_jobs"] = {"private-provider": "Needs private access."}
        self.record["run"]["conclusion"] = "failure"
        self.assert_blocked()

    def test_exclusions_do_not_hide_identity_mismatch(self):
        self.requirement["excluded_jobs"] = {"private-provider": "Needs private access."}
        private = deepcopy(self.record["jobs"][0])
        private.update(id=299, name="private-provider", conclusion="failure", head_sha=OTHER_SHA)
        self.record["jobs"].append(private)
        self.record["run"]["conclusion"] = "failure"
        self.assert_blocked()

    def test_job_identity_is_bound_to_exact_run_attempt_and_commit(self):
        for field, value in (
            ("head_sha", OTHER_SHA), ("run_id", 999), ("run_attempt", 1),
            ("id", 0), ("id", True),
        ):
            with self.subTest(field=field, value=value):
                self.evidence = evidence_fixture()
                self.record["jobs"][0][field] = value
                self.assert_refused()

    def test_boolean_job_identity_cannot_impersonate_integer_one(self):
        self.record["run"].update(id=1, run_attempt=1)
        self.record["artifacts"][0]["workflow_run"]["id"] = 1
        for job in self.record["jobs"]:
            job.update(run_id=1, run_attempt=1)
        for field in ("run_id", "run_attempt"):
            with self.subTest(field=field):
                self.record["jobs"][0][field] = True
                self.assert_refused()
                self.record["jobs"][0][field] = 1

    def test_duplicate_job_names_and_ids_are_refused(self):
        self.record["jobs"].append(deepcopy(self.record["jobs"][0]))
        self.assert_refused()
        self.evidence = evidence_fixture()
        self.record["jobs"][1]["id"] = self.record["jobs"][0]["id"]
        self.assert_refused()

    def test_required_artifact_missing_duplicate_or_renamed_is_blocked(self):
        artifact = deepcopy(self.record["artifacts"][0])
        for artifacts in ([], [artifact, deepcopy(artifact)], [{**artifact, "name": "old-evidence"}]):
            with self.subTest(artifacts=artifacts):
                self.record["artifacts"] = artifacts
                self.assert_blocked()

    def test_artifact_identity_cannot_satisfy_two_required_names(self):
        self.requirement["artifacts"].append("different-evidence")
        alias = deepcopy(self.record["artifacts"][0])
        alias["name"] = "different-evidence"
        self.record["artifacts"].append(alias)
        report = self.assert_blocked()
        self.assertIn("invalid or duplicate artifact identities", report["workflows"][0]["reasons"])

    def test_artifact_identity_validation_matches_online_pagination(self):
        for identity in (None, True, 0, "302", 301, []):
            with self.subTest(identity=identity):
                self.evidence = evidence_fixture()
                extra = deepcopy(self.record["artifacts"][0])
                extra.update(id=identity, name="unselected-artifact")
                self.record["artifacts"].append(extra)
                self.assert_blocked()
        self.evidence = evidence_fixture()
        extra = deepcopy(self.record["artifacts"][0])
        extra.update(id=302, name="unselected-artifact")
        self.record["artifacts"].append(extra)
        self.assertEqual(self.report()["status"], "ci_pass")

    def test_artifact_expiry_size_identity_and_attempt_time_are_required(self):
        for field, value in (
            ("id", 0), ("id", True), ("expired", True), ("expired", None),
            ("size_in_bytes", 0), ("size_in_bytes", True),
            ("workflow_run", {"id": 999, "head_sha": SHA}),
            ("workflow_run", {"id": 101, "head_sha": OTHER_SHA}),
            ("workflow_run", None),
            ("created_at", "2026-10-03T09:59:59Z"),
            ("created_at", "2026-10-03T10:05:00"),
            ("created_at", "not-a-time"),
            ("created_at", None),
        ):
            with self.subTest(field=field, value=value):
                self.evidence = evidence_fixture()
                self.record["artifacts"][0][field] = value
                self.assert_blocked()

    def test_old_attempt_artifact_does_not_shadow_current_evidence(self):
        old = deepcopy(self.record["artifacts"][0])
        old.update(id=300, created_at="2026-10-03T09:05:00Z", expired=True)
        self.record["artifacts"].insert(0, old)
        self.assertEqual(self.report()["status"], "ci_pass")

    def test_two_current_attempt_artifacts_are_ambiguous(self):
        duplicate = deepcopy(self.record["artifacts"][0])
        duplicate.update(id=302, created_at="2026-10-03T10:06:00Z")
        self.record["artifacts"].append(duplicate)
        self.assert_blocked()

    def test_artifact_after_run_completion_and_invalid_run_window_are_blocked(self):
        self.record["artifacts"][0]["created_at"] = "2026-10-03T10:10:01Z"
        self.assert_blocked()
        self.evidence = evidence_fixture()
        self.record["run"]["updated_at"] = "2026-10-03T09:59:59Z"
        self.assert_blocked()

    def test_malformed_old_named_artifact_cannot_be_ignored(self):
        old = deepcopy(self.record["artifacts"][0])
        old.update(id=300, created_at="invalid")
        self.record["artifacts"].append(old)
        self.assert_blocked()

    def test_artifact_timestamp_compares_instants_not_strings(self):
        self.record["artifacts"][0]["created_at"] = "2026-10-03T06:05:00-04:00"
        self.assertEqual(self.report()["status"], "ci_pass")
        self.record["artifacts"][0]["created_at"] = "2026-10-03T11:59:59+02:00"
        self.assert_blocked()

    def test_malformed_workflow_job_and_artifact_collections_fail_closed(self):
        for field, values in (
            ("jobs", (None, {}, ["not a job"])),
            ("artifacts", (None, {}, ["not an artifact"])),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.evidence = evidence_fixture()
                    self.record[field] = value
                    self.assert_refused()
        for workflows in (None, {}, [None], [deepcopy(self.record), deepcopy(self.record)]):
            with self.subTest(workflows=workflows):
                self.evidence = evidence_fixture()
                self.evidence["workflows"] = workflows
                self.assert_refused()

    def test_collection_error_cannot_be_overridden_by_success_records(self):
        self.record["error"] = "incomplete GitHub jobs pagination"
        self.assert_blocked()


    def test_workflow_cannot_reuse_another_workflows_run_job_or_artifact_identity(self):
        for identity in ("run", "job", "artifact"):
            with self.subTest(identity=identity):
                self.policy, self.evidence = policy_fixture(), evidence_fixture()
                requirement = deepcopy(self.requirement)
                requirement["path"] = ".github/workflows/second.yml"
                self.policy["required_workflows"].append(requirement)
                second = deepcopy(self.record)
                second["path"] = second["run"]["path"] = requirement["path"]
                second["run"]["id"] = 1001
                for job in second["jobs"]:
                    job["id"] += 1000
                    job["run_id"] = 1001
                second["artifacts"][0]["id"] += 1000
                second["artifacts"][0]["workflow_run"]["id"] = 1001
                self.evidence["workflows"].append(second)
                self.assertEqual(self.report()["status"], "ci_pass")
                if identity == "run":
                    second["run"]["id"] = self.record["run"]["id"]
                    for job in second["jobs"]:
                        job["run_id"] = self.record["run"]["id"]
                    second["artifacts"][0]["workflow_run"]["id"] = self.record["run"]["id"]
                elif identity == "job":
                    second["jobs"][0]["id"] = self.record["jobs"][0]["id"]
                else:
                    second["artifacts"][0]["id"] = self.record["artifacts"][0]["id"]
                self.assert_refused()



class PolicyAndRunSelectionTests(unittest.TestCase):
    def test_candidate_requires_full_lowercase_sha(self):
        self.assertEqual(gate.candidate_sha(SHA), SHA)
        for value in (SHA[:8], SHA.upper(), "main", "refs/heads/main", "g" * 40, None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gate.candidate_sha(value)

    def test_policy_rejects_conflicting_and_malformed_scope(self):
        cases = []
        policy = policy_fixture()
        policy["schema_version"] = True
        cases.append(policy)
        policy = policy_fixture()
        policy["required_workflows"] = []
        cases.append(policy)
        policy = policy_fixture()
        policy["repository"] = "example/engineering/extra"
        cases.append(policy)
        for field, value in (
            ("path", ".github/workflows/../other.yml"),
            ("jobs", []),
            ("jobs", ["build", "build"]),
            ("allowed_skipped_jobs", ["build"]),
            ("excluded_jobs", {"build": "conflicts with required"}),
            ("excluded_jobs", {"slow-reproduction": "conflicts with optional"}),
            ("excluded_jobs", {"private-provider": ""}),
            ("artifacts", ["evidence", "evidence"]),
        ):
            policy = policy_fixture()
            policy["required_workflows"][0][field] = value
            cases.append(policy)
        policy = policy_fixture()
        policy["required_workflows"].append(deepcopy(policy["required_workflows"][0]))
        cases.append(policy)
        for policy in cases:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                gate.validate_policy(policy)

    def test_newest_run_is_selected_even_when_it_failed(self):
        old = run_fixture()
        old["id"] = 100
        newest = run_fixture()
        newest["conclusion"] = "failure"
        chosen = gate.select_run([newest, old], SHA, REPOSITORY, WORKFLOW)
        self.assertIs(chosen, newest)

    def test_latest_attempt_wins_and_queued_rerun_does_not_fall_back(self):
        previous = run_fixture()
        newest = deepcopy(previous)
        newest.update(run_attempt=3, status="queued", conclusion=None)
        chosen = gate.select_run([previous, newest], SHA, REPOSITORY, WORKFLOW)
        self.assertIs(chosen, newest)

    def test_selection_ignores_ineligible_runs_not_wrong_commit_successes(self):
        valid = run_fixture()
        for field, value in (
            ("head_sha", OTHER_SHA), ("head_repository", {"full_name": "fork/engineering"}),
            ("path", ".github/workflows/other.yml"), ("event", "pull_request"),
        ):
            wrong = deepcopy(valid)
            wrong.update(id=999)
            wrong[field] = value
            with self.subTest(field=field):
                self.assertIs(gate.select_run([valid, wrong], SHA, REPOSITORY, WORKFLOW), valid)
                self.assertIsNone(gate.select_run([wrong], SHA, REPOSITORY, WORKFLOW))

    def test_invalid_eligible_run_identity_is_rejected(self):
        for field, value in (("id", True), ("id", 0), ("run_attempt", True), ("run_attempt", -1)):
            run = run_fixture()
            run[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                gate.select_run([run], SHA, REPOSITORY, WORKFLOW)


class PaginationAndCollectionTests(unittest.TestCase):
    def pages(self, responses):
        client = gate.GitHub(REPOSITORY)
        with mock.patch.object(client, "get", side_effect=responses) as get:
            rows = client.pages("actions/runs", "workflow_runs", {"head_sha": SHA})
        return rows, get

    def test_pagination_collects_all_pages_and_preserves_candidate_filter(self):
        rows, get = self.pages([
            {"total_count": 3, "workflow_runs": [{"id": 1}, {"id": 2}]},
            {"total_count": 3, "workflow_runs": [{"id": 3}]},
        ])
        self.assertEqual([row["id"] for row in rows], [1, 2, 3])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].args[1], {"head_sha": SHA, "per_page": 100, "page": 1})
        self.assertEqual(get.call_args_list[1].args[1]["page"], 2)

    def test_empty_complete_collection_is_valid(self):
        rows, get = self.pages([{"total_count": 0, "workflow_runs": []}])
        self.assertEqual(rows, [])
        self.assertEqual(get.call_count, 1)

    def test_incomplete_duplicate_excess_and_malformed_pagination_are_rejected(self):
        cases = [
            [{"total_count": 2, "workflow_runs": [{"id": 1}]},
             {"total_count": 2, "workflow_runs": []}],
            [{"total_count": 2, "workflow_runs": [{"id": 1}]},
             {"total_count": 2, "workflow_runs": [{"id": 1}]}],
            [{"total_count": 1, "workflow_runs": [{"id": 1}, {"id": 2}]}],
            [{"total_count": 1, "workflow_runs": [{"id": True}]}],
            [{"total_count": 1, "workflow_runs": [None]}],
            [{"total_count": True, "workflow_runs": []}],
            [{"total_count": -1, "workflow_runs": []}],
            [{"total_count": gate.MAX_PAGES * 100 + 1, "workflow_runs": []}],
            [{"total_count": 1, "workflow_runs": {}}],
            [[]],
        ]
        for responses in cases:
            with self.subTest(responses=responses), self.assertRaises(ValueError):
                self.pages(responses)

    def test_changing_total_cannot_turn_partial_snapshot_into_complete_one(self):
        with self.assertRaises(ValueError):
            self.pages([
                {"total_count": 3, "workflow_runs": [{"id": 1}, {"id": 2}]},
                {"total_count": 2, "workflow_runs": []},
            ])

    def test_pagination_stops_at_explicit_bound(self):
        with mock.patch.object(gate, "MAX_PAGES", 2), self.assertRaises(ValueError):
            self.pages([
                {"total_count": 3, "workflow_runs": [{"id": 1}]},
                {"total_count": 3, "workflow_runs": [{"id": 2}]},
            ])

    def api_fixture(self):
        record = evidence_fixture()["workflows"][0]
        api = mock.Mock()
        api.pages.side_effect = [[record["run"]], record["jobs"], record["artifacts"],
                                 [deepcopy(record["run"])]]
        api.get.return_value = deepcopy(record["run"])
        return api

    def test_collection_uses_exact_attempt_endpoint_and_rechecks_run(self):
        api = self.api_fixture()
        evidence = gate.collect(policy_fixture(), SHA, api)
        self.assertEqual(gate.evaluate(policy_fixture(), evidence, SHA)["status"], "ci_pass")
        self.assertEqual(api.pages.call_args_list[0].args,
                         ("actions/workflows/release.yml/runs", "workflow_runs", {"head_sha": SHA}))
        self.assertEqual(api.pages.call_args_list[1].args,
                         ("actions/runs/101/attempts/2/jobs", "jobs"))
        self.assertEqual(api.pages.call_args_list[2].args,
                         ("actions/runs/101/artifacts", "artifacts"))
        self.assertEqual(api.pages.call_args_list[3].args,
                         ("actions/workflows/release.yml/runs", "workflow_runs", {"head_sha": SHA}))
        api.get.assert_called_once_with("actions/runs/101")

    def test_new_workflow_run_inserted_during_collection_is_blocked(self):
        api = self.api_fixture()
        record = evidence_fixture()["workflows"][0]
        newest = deepcopy(record["run"])
        newest.update(id=102, status="queued", conclusion=None)
        api.pages.side_effect = [
            [record["run"]], record["jobs"], record["artifacts"],
            [record["run"], newest],
        ]
        evidence = gate.collect(policy_fixture(), SHA, api)
        self.assertIn("error", evidence["workflows"][0])
        self.assertEqual(gate.evaluate(policy_fixture(), evidence, SHA)["status"], "blocked")

    def test_run_changing_during_collection_is_blocked(self):
        for field, value in (
            ("run_attempt", 3), ("status", "in_progress"), ("conclusion", "failure"),
            ("updated_at", "2026-10-03T10:12:00Z"),
        ):
            api = self.api_fixture()
            api.get.return_value[field] = value
            with self.subTest(field=field):
                evidence = gate.collect(policy_fixture(), SHA, api)
                self.assertIn("error", evidence["workflows"][0])
                self.assertEqual(gate.evaluate(policy_fixture(), evidence, SHA)["status"], "blocked")

    def test_collection_preserves_pagination_failure_and_malformed_latest_record(self):
        api = self.api_fixture()
        api.pages.side_effect = ValueError("incomplete GitHub workflow_runs pagination")
        evidence = gate.collect(policy_fixture(), SHA, api)
        self.assertEqual(gate.evaluate(policy_fixture(), evidence, SHA)["status"], "blocked")
        for malformed in (None, [], "not a run"):
            api = self.api_fixture()
            api.get.return_value = malformed
            with self.subTest(malformed=malformed):
                evidence = gate.collect(policy_fixture(), SHA, api)
                self.assertIn("error", evidence["workflows"][0])
                self.assertEqual(gate.evaluate(policy_fixture(), evidence, SHA)["status"], "blocked")


class OfflineCommandTests(unittest.TestCase):
    def test_strict_json_refuses_duplicate_keys_and_nonfinite_constants(self):
        for raw in (
            '{"candidate_sha":"a","candidate_sha":"b"}',
            '{"nested":{"status":"success","status":"failure"}}',
            '{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}',
            '{"value":1e999}', '{"nested":[-1e999]}',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                gate.strict_json(raw)

    def test_strict_json_preserves_finite_exponents(self):
        self.assertEqual(gate.strict_json('{"values":[1.5e2,-2.5e-3,0.0]}'),
                         {"values": [150.0, -0.0025, 0.0]})

    def test_read_json_enforces_byte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_bytes(b'{"long":"1234567890"}')
            with mock.patch.object(gate, "MAX_BYTES", 10), self.assertRaises(ValueError):
                gate.read_json(path)

    def invoke(self, root, evidence, sha=SHA):
        root = Path(root)
        policy_path = root / "policy.json"
        evidence_path = root / "snapshot.json"
        output = root / "reports"
        policy_path.write_text(json.dumps(policy_fixture()), encoding="utf-8")
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(gate, "GitHub", side_effect=AssertionError("offline attempted network")), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = gate.main([
                "--sha", sha, "--policy", str(policy_path),
                "--evidence", str(evidence_path), "--output", str(output),
            ])
        return code, output, stdout.getvalue(), stderr.getvalue()

    def test_offline_success_writes_retained_snapshot_and_report_without_network(self):
        evidence = evidence_fixture()
        with tempfile.TemporaryDirectory() as directory:
            code, output, stdout, stderr = self.invoke(directory, evidence)
            self.assertEqual(code, 0, stderr)
            self.assertIn("ci_pass", stdout)
            self.assertEqual(json.loads((output / "evidence.json").read_text(encoding="utf-8")), evidence)
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "ci_pass")
            self.assertEqual(report["evidence_mode"], "offline_snapshot")
            self.assertEqual(report["candidate_sha"], SHA)
            self.assertEqual(set(path.name for path in output.iterdir()), {"evidence.json", "report.json"})

    def test_offline_blocked_evidence_writes_report_and_returns_one(self):
        evidence = evidence_fixture()
        evidence["workflows"][0]["jobs"][0]["conclusion"] = "failure"
        with tempfile.TemporaryDirectory() as directory:
            code, output, stdout, stderr = self.invoke(directory, evidence)
            self.assertEqual(code, 1, stderr)
            self.assertIn("blocked", stdout)
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "blocked")

    def test_existing_output_is_refused_without_overwriting_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reports"
            output.mkdir()
            existing = output / "evidence.json"
            original = b'{"retained":"original evidence"}\n'
            existing.write_bytes(original)
            code, actual_output, stdout, stderr = self.invoke(directory, evidence_fixture())
            self.assertEqual(code, 2)
            self.assertEqual(actual_output, output)
            self.assertEqual(existing.read_bytes(), original)
            self.assertFalse((output / "report.json").exists())
            self.assertIn("output directory already exists", stderr)
            self.assertNotIn("ci_pass:", stdout)

    def test_offline_malformed_snapshot_or_candidate_returns_two(self):
        for evidence, sha in (([], SHA), (None, SHA), (evidence_fixture(), "main")):
            with self.subTest(evidence=evidence, sha=sha), tempfile.TemporaryDirectory() as directory:
                code, output, stdout, stderr = self.invoke(directory, evidence, sha)
                self.assertEqual(code, 2)
                self.assertIn("release evidence error", stderr)
                self.assertFalse((output / "report.json").exists())
                self.assertNotIn("ci_pass:", stdout)


class RefurbishmentReleasePolicyTests(unittest.TestCase):
    """The public candidate must include the new software lanes, without skips."""

    NEW_LANES = (".github/workflows/hardware.yml", ".github/workflows/refurbishment.yml")

    def setUp(self):
        self.policy = gate.read_json(Path(__file__).resolve().parents[1] / "release/qualification-policy.json")
        gate.validate_policy(self.policy)
        self.evidence = evidence_fixture()
        self.evidence["repository"] = self.policy["repository"]
        self.evidence["workflows"] = []
        for workflow_index, requirement in enumerate(self.policy["required_workflows"]):
            record = deepcopy(evidence_fixture()["workflows"][0])
            record["path"] = record["run"]["path"] = requirement["path"]
            record["run"]["id"] = 1001 + workflow_index
            record["run"]["head_repository"]["full_name"] = self.policy["repository"]
            job = record["jobs"][0]
            record["jobs"] = [dict(job, id=100000 + workflow_index * 100 + index,
                                  name=name, run_id=record["run"]["id"])
                              for index, name in enumerate(requirement["jobs"] + requirement["allowed_skipped_jobs"])]
            for job in record["jobs"]:
                if job["name"] in requirement["allowed_skipped_jobs"]:
                    job["conclusion"] = "skipped"
            artifact = record["artifacts"][0]
            record["artifacts"] = [dict(artifact, id=200000 + workflow_index * 100 + index,
                                       name=name, workflow_run={"id": record["run"]["id"], "head_sha": SHA})
                                   for index, name in enumerate(requirement["artifacts"])]
            self.evidence["workflows"].append(record)

    def test_complete_candidate_includes_both_platforms_and_python_versions(self):
        self.assertEqual(gate.evaluate(self.policy, self.evidence, SHA)["status"], "ci_pass")
        for path, job_prefix, artifact_prefix in (
            (self.NEW_LANES[0], "qualification", "hardware"),
            (self.NEW_LANES[1], "installed-journey", "refurbishment"),
        ):
            requirement = next(item for item in self.policy["required_workflows"] if item["path"] == path)
            self.assertEqual(requirement["excluded_jobs"], {})
            self.assertEqual(requirement["allowed_skipped_jobs"], [])
            for os_name in ("ubuntu-24.04", "windows-latest"):
                for python in ("3.11", "3.12"):
                    self.assertIn(f"{job_prefix} ({os_name}, {python})", requirement["jobs"])
                    self.assertIn(f"{artifact_prefix}-{os_name}-{python}", requirement["artifacts"])
        wheel = next(item for item in self.policy["required_workflows"] if item["path"] == self.NEW_LANES[1])
        self.assertIn("candidate-wheel", wheel["jobs"])
        self.assertIn("refurbishment-candidate-wheel", wheel["artifacts"])

    def test_current_web_matrix_jobs_and_artifacts_match_workflow(self):
        # Read the live definition, not a fixture copied from the release policy.
        # This intentionally accepts the current single-axis literal matrix only;
        # a more complex workflow must update the contract check explicitly.
        path = ".github/workflows/monorepo.yml"
        workflow = (ROOT / path).read_text(encoding="utf-8")
        job_id = "independent-locked-web-packages"
        match = re.search(r"(?ms)^  " + job_id + r":\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", workflow)
        self.assertIsNotNone(match)
        body = match.group(1)
        self.assertNotRegex(body, r"(?m)^    name:")
        matrix = re.search(r"(?m)^      matrix:\n((?:        .*\n)+)", body)
        self.assertIsNotNone(matrix)
        platforms = re.fullmatch(r"        os: \[([^]\n]+)\]\n", matrix.group(1))
        self.assertIsNotNone(platforms)
        operating_systems = [value.strip().strip("\"'") for value in platforms.group(1).split(",")]
        self.assertTrue(operating_systems)
        self.assertEqual(len(operating_systems), len(set(operating_systems)))
        self.assertIn("    runs-on: ${{ matrix.os }}\n", body)
        artifact_templates = re.findall(r"(?m)^          name: (monorepo-web-[^\n]+)$", body)
        self.assertEqual(len(artifact_templates), 1)
        artifact_template = artifact_templates[0]
        self.assertIn("${{ matrix.os }}", artifact_template)
        requirement = next(item for item in self.policy["required_workflows"] if item["path"] == path)
        for field, prefix, expected in (
            ("jobs", job_id, {f"{job_id} ({platform})" for platform in operating_systems}),
            ("artifacts", "monorepo-web-", {artifact_template.replace("${{ matrix.os }}", platform)
                                           for platform in operating_systems}),
        ):
            with self.subTest(field=field):
                actual = {name for name in requirement[field] if name.startswith(prefix)}
                self.assertEqual(actual, expected)
        self.assertFalse(any(name.startswith(job_id) for name in requirement["allowed_skipped_jobs"]))
        self.assertFalse(any(name.startswith(job_id) for name in requirement["excluded_jobs"]))

    def test_missing_new_workflow_blocks_release(self):
        for path in self.NEW_LANES:
            with self.subTest(path=path):
                evidence = deepcopy(self.evidence)
                evidence["workflows"] = [item for item in evidence["workflows"] if item["path"] != path]
                self.assertEqual(gate.evaluate(self.policy, evidence, SHA)["status"], "blocked")

    def test_every_new_job_must_succeed_and_retain_its_artifacts(self):
        for workflow_index, record in enumerate(self.evidence["workflows"]):
            if record["path"] not in self.NEW_LANES:
                continue
            for job_index, job in enumerate(record["jobs"]):
                for conclusion in ("failure", "skipped", "neutral", "cancelled"):
                    with self.subTest(path=record["path"], job=job["name"], conclusion=conclusion):
                        evidence = deepcopy(self.evidence)
                        evidence["workflows"][workflow_index]["jobs"][job_index]["conclusion"] = conclusion
                        self.assertEqual(gate.evaluate(self.policy, evidence, SHA)["status"], "blocked")
            for artifact_index, artifact in enumerate(record["artifacts"]):
                with self.subTest(path=record["path"], missing_artifact=artifact["name"]):
                    evidence = deepcopy(self.evidence)
                    del evidence["workflows"][workflow_index]["artifacts"][artifact_index]
                    self.assertEqual(gate.evaluate(self.policy, evidence, SHA)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
