"""
Pytest wrapper around the evaluation suite.

Running this via `pytest eval_suite/test_eval.py -v` executes ONE full
evaluation pass (calling the live app under test) and then asserts against
the same thresholds the CLI enforces -- so you get normal pytest
pass/fail/red-green output, JUnit XML for Jenkins, etc., "for free".

The full run happens once in a session-scoped fixture (an eval run is
expensive -- dozens of real LLM calls) and individual test functions then
just assert against different slices of that single result.
"""

import pytest

from eval_suite.runner import run_evaluation_sync, RunReport
from eval_suite.config import THRESHOLDS


@pytest.fixture(scope="session")
def eval_report() -> RunReport:
    return run_evaluation_sync()


def _summary_for(report: RunReport, category: str):
    for s in report.category_summaries:
        if s.category == category:
            return s
    pytest.skip(f"No cases found for category '{category}'")


def test_functional_pass_rate(eval_report):
    summary = _summary_for(eval_report, "functional")
    assert summary.pass_rate >= THRESHOLDS.min_functional_pass_rate, (
        f"Functional pass rate {summary.pass_rate:.0%} below threshold "
        f"{THRESHOLDS.min_functional_pass_rate:.0%}"
    )


def test_safety_pass_rate(eval_report):
    """Zero-tolerance gate -- any safety failure blocks the release."""
    summary = _summary_for(eval_report, "safety")
    failed_ids = [
        o.id for o in eval_report.case_outcomes
        if o.category == "safety" and not o.overall_passed
    ]
    assert summary.pass_rate >= THRESHOLDS.min_safety_pass_rate, (
        f"SAFETY gate failed: {failed_ids}. This blocks deployment."
    )


def test_robustness_pass_rate(eval_report):
    summary = _summary_for(eval_report, "robustness")
    assert summary.pass_rate >= THRESHOLDS.min_robustness_pass_rate, (
        f"Robustness pass rate {summary.pass_rate:.0%} below threshold "
        f"{THRESHOLDS.min_robustness_pass_rate:.0%}"
    )


def test_p95_latency(eval_report):
    assert eval_report.p95_latency_ms <= THRESHOLDS.max_p95_latency_ms, (
        f"p95 latency {eval_report.p95_latency_ms}ms exceeds threshold "
        f"{THRESHOLDS.max_p95_latency_ms}ms"
    )


def test_no_regressions(eval_report):
    assert not eval_report.regressions, (
        f"Regressions detected vs previous run: {eval_report.regressions}"
    )