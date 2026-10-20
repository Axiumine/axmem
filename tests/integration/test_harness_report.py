"""Integration tests for the UAT harness report contract (test_harness/report.py).

The walkthrough agent writes report.json in its workspace; the harness must parse it
tolerantly — a malformed or missing report is a recorded finding about the run, never a
harness crash (the eval-harness trust boundary: only harness-side defects raise).
"""
import json

from test_harness.report import (
    WalkthroughReport,
    load_report,
    report_contract_example,
)

VALID_REPORT = {
    "skill": "axmem-recall",
    "verdict": "issues",
    "steps": [
        {
            "step": "Query shaping",
            "commands": ["axmem call query_memory --args '{\"query\": \"x\"}' --json"],
            "observed": "Returned primary_memories with 3 hits",
            "verdict": "ok",
        },
    ],
    "issues": [
        {
            "severity": "minor",
            "where": "Step 2 - scoping",
            "what": "Skill says truncated flag, response field is actually 'truncated'",
            "evidence": "query_memory response keys: [...]",
        },
    ],
}


def test_valid_report_parses(tmp_path):
    # Arrange
    path = tmp_path / "report.json"
    path.write_text(json.dumps(VALID_REPORT))

    # Act
    outcome = load_report(path)

    # Assert
    assert outcome.status == "ok"
    assert outcome.report is not None
    assert outcome.report.skill == "axmem-recall"
    assert outcome.report.verdict == "issues"
    assert outcome.report.steps[0].verdict == "ok"
    assert outcome.report.issues[0].severity == "minor"


def test_contract_example_round_trips():
    # Arrange: the example embedded in the walkthrough prompt is the single source of
    # truth for the contract - it must always satisfy the parser.
    example = report_contract_example()

    # Act
    parsed = WalkthroughReport.model_validate(json.loads(example))

    # Assert
    assert parsed.skill
    assert parsed.verdict in ("pass", "issues", "blocked")


def test_missing_report_is_recorded_finding(tmp_path):
    # Arrange
    path = tmp_path / "report.json"

    # Act
    outcome = load_report(path)

    # Assert
    assert outcome.status == "missing"
    assert outcome.report is None


def test_invalid_json_is_recorded_finding(tmp_path):
    # Arrange
    path = tmp_path / "report.json"
    path.write_text("{not valid json")

    # Act
    outcome = load_report(path)

    # Assert
    assert outcome.status == "invalid_json"
    assert outcome.report is None
    assert outcome.detail


def test_schema_violation_is_recorded_finding(tmp_path):
    # Arrange: verdict missing entirely
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"skill": "axmem-recall", "issues": []}))

    # Act
    outcome = load_report(path)

    # Assert
    assert outcome.status == "schema_error"
    assert outcome.report is None
    assert "verdict" in outcome.detail


def test_enum_fields_normalize_case(tmp_path):
    # Arrange: weaker models capitalise enum values; that is not a schema violation
    report = dict(VALID_REPORT, verdict="Pass")
    report["issues"] = [dict(VALID_REPORT["issues"][0], severity="MAJOR")]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))

    # Act
    outcome = load_report(path)

    # Assert
    assert outcome.status == "ok"
    assert outcome.report.verdict == "pass"
    assert outcome.report.issues[0].severity == "major"


def test_step_verdict_accepts_plural_alias(tmp_path):
    # Arrange: first live run wrote step verdicts as "issues" (the report-level enum
    # bleeding into steps) - the intent is unambiguous, so tolerate the alias
    report = dict(VALID_REPORT)
    report["steps"] = [dict(VALID_REPORT["steps"][0], verdict="issues")]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))

    # Act
    outcome = load_report(path)

    # Assert
    assert outcome.status == "ok"
    assert outcome.report.steps[0].verdict == "issue"


def test_severity_accepts_common_aliases(tmp_path):
    # Arrange: the full live run wrote severity "trivial" twice (and "critical" is the
    # same class of near-miss) - both map unambiguously onto the contract's scale
    report = dict(VALID_REPORT)
    report["issues"] = [
        dict(VALID_REPORT["issues"][0], severity="trivial"),
        dict(VALID_REPORT["issues"][0], severity="critical"),
    ]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))

    # Act
    outcome = load_report(path)

    # Assert
    assert outcome.status == "ok"
    assert outcome.report.issues[0].severity == "nit"
    assert outcome.report.issues[1].severity == "blocker"


def test_report_verdict_accepts_singular_alias(tmp_path):
    # Arrange: the symmetric confusion - step-level "issue" written at report level
    report = dict(VALID_REPORT, verdict="issue")
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))

    # Act
    outcome = load_report(path)

    # Assert
    assert outcome.status == "ok"
    assert outcome.report.verdict == "issues"


def test_extra_fields_are_ignored(tmp_path):
    # Arrange
    report = dict(VALID_REPORT, notes="unsolicited commentary", confidence=0.9)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))

    # Act
    outcome = load_report(path)

    # Assert
    assert outcome.status == "ok"
