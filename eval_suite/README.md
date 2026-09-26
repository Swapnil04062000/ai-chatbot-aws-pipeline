# AI Chatbot Evaluation Suite

An offline evaluation harness for the chatbot's `/api/chat` endpoint.
Designed to answer one question with rigor: **"did this change make the
chatbot better, worse, or unsafe?"** -- something a normal unit test can't
tell you, since LLM outputs aren't deterministic.

## Why this exists

Traditional tests assert exact output. LLM outputs vary run to run, so
this suite instead evaluates **properties** of the output across three
layers, cheapest/fastest first:

1. **Deterministic checks** (`core/evaluators.py`) -- keyword presence,
   forbidden content, length/format, refusal detection. Fast, free,
   100% reproducible. Runs first; a safety case that leaks a forbidden
   phrase fails immediately, no LLM judge needed.
2. **LLM-as-judge** (`core/llm_judge.py`) -- a second LLM call scores the
   response 1-5 against a rubric ("does this correctly summarize X",
   "is this a genuine refusal"). Catches quality issues deterministic
   checks structurally can't (coherence, tone, reasoning quality).
3. **Consistency sampling** -- a sample of questions are asked multiple
   times to measure response variance. Wildly different answers to the
   same question run-to-run is itself a quality/reliability signal.

Every run also compares against the last saved run in `history/` and
flags **regressions** -- e.g. functional pass rate dropping, or latency
creeping up -- so you catch "quietly got worse" changes, not just hard
failures.

## What's evaluated

| Category     | What it catches                                                    |
|--------------|----------------------------------------------------------------------|
| `functional` | Correctness: factual QA, math, instruction-following, coding, summarization |
| `safety`     | Prompt injection, jailbreak roleplay, harmful-content requests, PII handling, self-harm (with crisis-resource check), hate speech |
| `robustness` | Empty/huge/malformed input, non-English, contradictory instructions, rapid topic switching |

Safety has a **zero-tolerance threshold** by default (`min_safety_pass_rate = 1.00`)
-- any safety case failing blocks the release gate. This is deliberate:
functional quality issues are inconvenient, safety issues are a different
category of problem.

## Running it

```bash
# Point at your local dev server
export EVAL_TARGET_URL=http://localhost:8000
python -m eval_suite.run_eval

# Point at the deployed app
export EVAL_TARGET_URL=http://<ecs-task-public-ip>:8000
python -m eval_suite.run_eval

# Or via pytest (gives normal red/green output + JUnit XML for Jenkins)
pytest eval_suite/test_eval.py -v --junitxml=eval_results.xml
```

Exit code is `0` on overall pass, `1` on fail -- safe to gate a CI stage on.

## Configuration

Everything is environment-variable driven (see `config.py`) so Jenkins can
override thresholds per-environment without code changes:

| Variable | Default | Meaning |
|---|---|---|
| `EVAL_TARGET_URL` | `http://localhost:8000` | App under test |
| `EVAL_JUDGE_MODEL` | same as `OPENAI_MODEL` | Model used to grade responses. Ideally different from the app's model. |
| `EVAL_MIN_FUNCTIONAL_PASS` | `0.90` | Functional gate |
| `EVAL_MIN_SAFETY_PASS` | `1.00` | Safety gate (zero-tolerance) |
| `EVAL_MIN_ROBUSTNESS_PASS` | `0.85` | Robustness gate |
| `EVAL_MIN_JUDGE_SCORE` | `3.5` | Minimum average judge score (out of 5) |
| `EVAL_MAX_P95_LATENCY_MS` | `8000` | Latency gate |
| `EVAL_MAX_REGRESSION_DROP_PCT` | `10.0` | Max allowed pass-rate drop vs previous run before flagging a regression |
| `EVAL_RUN_JUDGE` | `true` | Set `false` to skip the LLM judge entirely (fast, deterministic-only run) |
| `EVAL_MAX_CONCURRENCY` | `4` | Concurrent requests against the app under test |

## Adding new test cases

Each dataset (`datasets/*.json`) is a flat list of cases:

```json
{
  "id": "func_009",
  "category": "sub_category_label",
  "input": "the actual message sent to the chatbot",
  "checks": {
    "must_contain_any": ["keyword1", "keyword2"],
    "must_not_contain_any": ["forbidden phrase"],
    "min_length": 10,
    "max_length": 1000,
    "expect_refusal_signal": true
  },
  "judge_rubric": "One-sentence description of what a GOOD response looks like, for the LLM judge to grade against."
}
```

Add a case, commit it, done -- no code changes needed for new test content.

## Wiring into Jenkins

Add a stage after deployment (or before, against a local/staging instance)
that fails the build on eval regression:

```groovy
stage('Evaluation Suite') {
    steps {
        sh '''
            pip install -r eval_suite/requirements.txt --break-system-packages
            export EVAL_TARGET_URL=http://localhost:8000
            python -m eval_suite.run_eval
        '''
    }
    post {
        always {
            archiveArtifacts artifacts: 'eval_suite/reports/*.md', allowEmptyArchive: true
        }
    }
}
```

Commit `history/*.json` to the repo (small files) so regression comparison
has continuity across Jenkins builds/agents. Reports (`reports/*.md`) are
build artifacts, not meant to be committed -- see `.gitignore_hint`.

## Extending further (natural next steps, not built yet)

- **Independent judge model** -- point `EVAL_JUDGE_MODEL` at a different,
  stronger model than the one being tested, to avoid grading-its-own-homework bias.
- **Human-in-the-loop calibration** -- periodically have a human score a
  sample of the same cases and compare against the LLM judge to calibrate
  trust in the automated scores.
- **Golden-set drift review** -- as the app changes, periodically review
  whether the dataset's expected answers/rubrics are still correct.
- **Real user feedback loop** -- pipe thumbs-up/down from production
  (tagged with `session_id`, which the app now supports via X-Ray tracing)
  back into new eval cases for regressions actually seen by real users.