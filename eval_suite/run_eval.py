#!/usr/bin/env python3
"""
CLI entrypoint for the evaluation suite.

Usage:
    python -m eval_suite.run_eval
    EVAL_TARGET_URL=http://54.235.16.213:8000 python -m eval_suite.run_eval

Exit code 0 = overall pass, exit code 1 = overall fail (gates CI on failure).
"""

import sys
import asyncio

from eval_suite.runner import run_evaluation
from eval_suite.report import render_markdown, save_report


async def main():
    print("Running evaluation suite...\n")
    report = await run_evaluation()

    markdown = render_markdown(report)
    print(markdown)

    saved_path = save_report(report)
    print(f"\nReport saved to: {saved_path}")

    if not report.overall_pass:
        print("\n### EVALUATION FAILED -- see gate failures / regressions above ###")
        sys.exit(1)
    else:
        print("\n### EVALUATION PASSED ###")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())