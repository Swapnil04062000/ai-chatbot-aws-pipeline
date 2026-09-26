"""
Renders a RunReport as a human-readable Markdown report -- meant to be
saved as a build artifact in Jenkins, or read directly on the terminal.
"""

import os
from eval_suite.runner import RunReport
from eval_suite.config import RUN


def render_markdown(report: RunReport) -> str:
    lines = []
    status_emoji = "PASS" if report.overall_pass else "FAIL"
    lines.append(f"# Evaluation Report -- {status_emoji}")
    lines.append("")
    lines.append(f"- **Run ID:** `{report.run_id}`")
    lines.append(f"- **Timestamp:** {report.timestamp}")
    lines.append(f"- **Target:** {report.target_url}")
    lines.append(f"- **Overall result:** {'PASS' if report.overall_pass else 'FAIL'}")
    lines.append("")

    lines.append("## Latency")
    lines.append("")
    lines.append(f"| p50 | p95 | p99 |")
    lines.append(f"|---|---|---|")
    lines.append(f"| {report.p50_latency_ms}ms | {report.p95_latency_ms}ms | {report.p99_latency_ms}ms |")
    lines.append("")

    lines.append("## Category Summary")
    lines.append("")
    lines.append("| Category | Total | Passed | Pass Rate | Avg Judge Score |")
    lines.append("|---|---|---|---|---|")
    for s in report.category_summaries:
        judge_str = f"{s.avg_judge_score}/5" if s.avg_judge_score is not None else "n/a"
        lines.append(f"| {s.category} | {s.total} | {s.passed} | {s.pass_rate:.0%} | {judge_str} |")
    lines.append("")

    if report.failures:
        lines.append("## Gate Failures")
        lines.append("")
        for f in report.failures:
            lines.append(f"- {f}")
        lines.append("")

    if report.regressions:
        lines.append("## Regressions vs Previous Run")
        lines.append("")
        for r in report.regressions:
            lines.append(f"- {r}")
        lines.append("")

    lines.append("## Consistency Check (repeat-question variance)")
    lines.append("")
    lines.append("| Case ID | Repeats | Unique Responses | Response Length StdDev |")
    lines.append("|---|---|---|---|")
    for c in report.consistency_outcomes:
        flag = " high variance" if c.unique_response_count == len(c.responses) and len(c.responses) > 1 else ""
        lines.append(f"| {c.id} | {len(c.responses)} | {c.unique_response_count}{flag} | {c.length_stddev} |")
    lines.append("")

    lines.append("## Failed Cases (detail)")
    lines.append("")
    failed = [o for o in report.case_outcomes if not o.overall_passed]
    if not failed:
        lines.append("_None -- all cases passed._")
    else:
        for o in failed:
            lines.append(f"### `{o.id}` ({o.category} / {o.sub_category})")
            lines.append(f"- **Input:** {o.input}")
            lines.append(f"- **Response:** {o.response[:300]}{'...' if len(o.response) > 300 else ''}")
            if o.error:
                lines.append(f"- **Error:** {o.error}")
            if o.deterministic_reasons:
                lines.append(f"- **Deterministic check failures:** {'; '.join(o.deterministic_reasons)}")
            if o.judge_score is not None:
                lines.append(f"- **Judge score:** {o.judge_score}/5 -- {o.judge_justification}")
            lines.append("")

    return "\n".join(lines)


def save_report(report: RunReport) -> str:
    os.makedirs(RUN.report_dir, exist_ok=True)
    
    # 1. Saves the unique run ID file (You fixed this one!)
    path = os.path.join(RUN.report_dir, f"{report.run_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_markdown(report))
        
    # 2. Also write a stable "latest.md" (Fix this one now)
    latest_path = os.path.join(RUN.report_dir, "latest.md")
    with open(latest_path, "w", encoding="utf-8") as f:  # <-- Added encoding here
        f.write(render_markdown(report))
        
    return path
