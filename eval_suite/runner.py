"""
Evaluation runner.

Orchestrates a full eval pass:
  1. Load every dataset (functional / safety / robustness).
  2. Run each case through the live app, with bounded concurrency.
  3. Apply deterministic checks; optionally apply the LLM judge.
  4. Re-run a sample of cases N times to measure output consistency
     (LLMs are non-deterministic -- wildly different answers to the same
     question on repeat calls is itself a quality signal worth tracking).
  5. Aggregate metrics (pass rate per category, judge score, latency
     percentiles).
  6. Compare against the last saved run in history/ to flag regressions.
  7. Persist this run to history/ for the next comparison.
  8. Return a RunReport with a clear overall pass/fail against configured
     thresholds -- this is what CI actually gates on.
"""

import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional

from eval_suite.config import TARGET, THRESHOLDS, RUN
from eval_suite.client import call_chat, ChatResult
from eval_suite.evaluators import run_deterministic_checks, CheckResult
from eval_suite.llm_judge import judge_response, JudgeResult


@dataclass
class CaseOutcome:
    id: str
    category: str
    sub_category: str
    input: str
    response: str
    http_ok: bool
    latency_ms: float
    deterministic_passed: bool
    deterministic_reasons: List[str]
    judge_score: Optional[int]
    judge_justification: str
    error: Optional[str] = None

    @property
    def overall_passed(self) -> bool:
        judge_ok = self.judge_score is None or self.judge_score >= 3
        return self.deterministic_passed and judge_ok and self.error is None


@dataclass
class ConsistencyOutcome:
    id: str
    input: str
    responses: List[str]
    unique_response_count: int
    length_stddev: float


@dataclass
class CategorySummary:
    category: str
    total: int
    passed: int
    pass_rate: float
    avg_judge_score: Optional[float]


@dataclass
class RunReport:
    run_id: str
    timestamp: str
    target_url: str
    case_outcomes: List[CaseOutcome] = field(default_factory=list)
    consistency_outcomes: List[ConsistencyOutcome] = field(default_factory=list)
    category_summaries: List[CategorySummary] = field(default_factory=list)
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    overall_pass: bool = False
    failures: List[str] = field(default_factory=list)  # human-readable gate failure reasons
    regressions: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _load_dataset(category: str) -> List[dict]:
    path = os.path.join(RUN.dataset_dir, f"{category}.json")
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


async def _run_single_case(sem: asyncio.Semaphore, case: dict, category: str) -> CaseOutcome:
    async with sem:
        user_input = case["input"]
        repeat_times = case.get("repeat_input_times")
        if repeat_times:
            user_input = user_input * repeat_times

        result: ChatResult = await call_chat(user_input)

        check: CheckResult = run_deterministic_checks(case, result.message, result.ok)

        judge_result = JudgeResult(score=None, justification="")
        if RUN.run_llm_judge and result.ok and result.message:
            judge_result = await judge_response(
                question=case["input"],
                response=result.message,
                rubric=case.get("judge_rubric", "N/A"),
            )

        return CaseOutcome(
            id=case["id"],
            category=category,
            sub_category=case.get("category", "uncategorized"),
            input=case["input"][:200],
            response=(result.message or "")[:2000],
            http_ok=result.ok,
            latency_ms=round(result.latency_ms, 1),
            deterministic_passed=check.passed,
            deterministic_reasons=check.reasons,
            judge_score=judge_result.score,
            judge_justification=judge_result.justification,
            error=result.error,
        )


async def _run_consistency_check(sem: asyncio.Semaphore, case: dict) -> ConsistencyOutcome:
    async with sem:
        responses = []
        for _ in range(RUN.repeat_for_consistency):
            result = await call_chat(case["input"])
            responses.append(result.message or "")
        lengths = [len(r) for r in responses]
        stddev = statistics.stdev(lengths) if len(lengths) > 1 else 0.0
        return ConsistencyOutcome(
            id=case["id"],
            input=case["input"][:200],
            responses=[r[:500] for r in responses],
            unique_response_count=len(set(responses)),
            length_stddev=round(stddev, 1),
        )


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * (pct / 100)
    f, c = int(k), min(int(k) + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def _load_previous_run(history_dir: str) -> Optional[dict]:
    if not os.path.isdir(history_dir):
        return None
    runs = sorted(
        [f for f in os.listdir(history_dir) if f.endswith(".json")],
        reverse=True,
    )
    if not runs:
        return None
    with open(os.path.join(history_dir, runs[0]), "r") as f:
        return json.load(f)


def _save_run(history_dir: str, report: RunReport):
    os.makedirs(history_dir, exist_ok=True)
    path = os.path.join(history_dir, f"{report.run_id}.json")
    with open(path, "w") as f:
        json.dump(report.to_dict(), f, indent=2)


async def run_evaluation() -> RunReport:
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    report = RunReport(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        target_url=TARGET.base_url,
    )

    sem = asyncio.Semaphore(TARGET.max_concurrency)

    # --- 1. Run every category's cases ---
    all_outcomes: List[CaseOutcome] = []
    for category in RUN.categories:
        cases = _load_dataset(category)
        tasks = [_run_single_case(sem, case, category) for case in cases]
        outcomes = await asyncio.gather(*tasks)
        all_outcomes.extend(outcomes)
    report.case_outcomes = all_outcomes

    # --- 2. Consistency sampling (functional cases only, small sample) ---
    functional_cases = _load_dataset("functional")
    sample = functional_cases[: RUN.consistency_sample_size]
    consistency_tasks = [_run_consistency_check(sem, case) for case in sample]
    report.consistency_outcomes = await asyncio.gather(*consistency_tasks)

    # --- 3. Aggregate per-category summaries ---
    by_category = {}
    for outcome in all_outcomes:
        by_category.setdefault(outcome.category, []).append(outcome)

    for category, outcomes in by_category.items():
        total = len(outcomes)
        passed = sum(1 for o in outcomes if o.overall_passed)
        judge_scores = [o.judge_score for o in outcomes if o.judge_score is not None]
        report.category_summaries.append(
            CategorySummary(
                category=category,
                total=total,
                passed=passed,
                pass_rate=round(passed / total, 4) if total else 0.0,
                avg_judge_score=round(statistics.mean(judge_scores), 2) if judge_scores else None,
            )
        )

    # --- 4. Latency percentiles (successful requests only) ---
    latencies = [o.latency_ms for o in all_outcomes if o.http_ok]
    report.p50_latency_ms = round(_percentile(latencies, 50), 1)
    report.p95_latency_ms = round(_percentile(latencies, 95), 1)
    report.p99_latency_ms = round(_percentile(latencies, 99), 1)

    # --- 5. Apply pass/fail gates ---
    failures = []
    for summary in report.category_summaries:
        if summary.category == "functional" and summary.pass_rate < THRESHOLDS.min_functional_pass_rate:
            failures.append(
                f"Functional pass rate {summary.pass_rate:.0%} below threshold "
                f"{THRESHOLDS.min_functional_pass_rate:.0%}"
            )
        if summary.category == "safety" and summary.pass_rate < THRESHOLDS.min_safety_pass_rate:
            failures.append(
                f"SAFETY pass rate {summary.pass_rate:.0%} below required "
                f"{THRESHOLDS.min_safety_pass_rate:.0%} -- treat as release-blocking"
            )
        if summary.category == "robustness" and summary.pass_rate < THRESHOLDS.min_robustness_pass_rate:
            failures.append(
                f"Robustness pass rate {summary.pass_rate:.0%} below threshold "
                f"{THRESHOLDS.min_robustness_pass_rate:.0%}"
            )
        if summary.avg_judge_score is not None and summary.avg_judge_score < THRESHOLDS.min_judge_score:
            failures.append(
                f"[{summary.category}] avg judge score {summary.avg_judge_score} below threshold "
                f"{THRESHOLDS.min_judge_score}"
            )

    if report.p95_latency_ms > THRESHOLDS.max_p95_latency_ms:
        failures.append(
            f"p95 latency {report.p95_latency_ms}ms exceeds threshold {THRESHOLDS.max_p95_latency_ms}ms"
        )

    report.failures = failures

    # --- 6. Regression check against previous run ---
    previous = _load_previous_run(RUN.history_dir)
    regressions = []
    if previous:
        prev_summaries = {s["category"]: s for s in previous.get("category_summaries", [])}
        for summary in report.category_summaries:
            prev = prev_summaries.get(summary.category)
            if prev and prev["pass_rate"] > 0:
                drop_pct = (prev["pass_rate"] - summary.pass_rate) / prev["pass_rate"] * 100
                if drop_pct > THRESHOLDS.max_regression_drop_pct:
                    regressions.append(
                        f"[{summary.category}] pass rate dropped {drop_pct:.1f}% "
                        f"vs previous run ({prev['pass_rate']:.0%} -> {summary.pass_rate:.0%})"
                    )
        prev_p95 = previous.get("p95_latency_ms", 0)
        if prev_p95 and report.p95_latency_ms > prev_p95 * 1.5:
            regressions.append(
                f"p95 latency increased {((report.p95_latency_ms / prev_p95) - 1) * 100:.0f}% "
                f"vs previous run ({prev_p95}ms -> {report.p95_latency_ms}ms)"
            )
    report.regressions = regressions

    report.overall_pass = len(failures) == 0 and len(regressions) == 0

    # --- 7. Persist this run for future regression comparisons ---
    _save_run(RUN.history_dir, report)

    return report


def run_evaluation_sync() -> RunReport:
    """Convenience sync wrapper (e.g. for pytest)."""
    return asyncio.run(run_evaluation())