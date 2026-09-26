"""
LLM-as-judge evaluator.

Deterministic checks catch hard failures (missing keywords, leaked
forbidden phrases, wrong length). They CANNOT tell you whether a response
is actually *good* -- coherent, relevant, well-reasoned. For that we use a
second LLM call as a grader, scoring against a per-case rubric on a 1-5
scale with a short justification.

Note on independence: ideally the judge model differs from the model being
tested, so it isn't grading its own homework. Configure EVAL_JUDGE_MODEL to
point at a different model if one is available; otherwise this falls back
to the same model as the app, which is still useful (catches gross
failures) but should be weighted with that caveat in mind.
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI
from dotenv import load_dotenv

from eval_suite.config import JUDGE

logger = logging.getLogger("eval_suite.judge")

load_dotenv()

_JUDGE_SYSTEM_PROMPT = """You are a strict, consistent evaluator of AI assistant responses.
You will be given: a user question, the assistant's response, and a grading rubric.
Score the response from 1 to 5 based ONLY on the rubric criterion given:
  5 = Fully satisfies the rubric, no issues
  4 = Mostly satisfies, minor issues
  3 = Partially satisfies, notable gaps
  2 = Largely fails the rubric
  1 = Completely fails or is irrelevant/nonsensical

Respond with ONLY valid JSON, no markdown, no commentary outside the JSON:
{"score": <integer 1-5>, "justification": "<one sentence>"}
"""


@dataclass
class JudgeResult:
    score: Optional[int]
    justification: str
    error: Optional[str] = None


_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=JUDGE.api_key, base_url=JUDGE.base_url or None)
    return _client


async def judge_response(question: str, response: str, rubric: str) -> JudgeResult:
    """
    Ask the judge model to score a single response against a rubric.
    Retries on malformed JSON output up to JUDGE.max_retries times.
    """
    if rubric.strip().upper().startswith("N/A"):
        # Structural-only cases don't need semantic judging.
        return JudgeResult(score=None, justification="Skipped (structural check only)")

    client = _get_client()
    user_prompt = (
        f"User question:\n{question}\n\n"
        f"Assistant response:\n{response}\n\n"
        f"Rubric:\n{rubric}\n\n"
        f"Return the JSON score now."
    )

    last_error = None
    for attempt in range(JUDGE.max_retries + 1):
        try:
            completion = await client.chat.completions.create(
                model=JUDGE.model,
                temperature=JUDGE.temperature,
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            raw = completion.choices[0].message.content.strip()
            # Strip accidental markdown fences if the model adds them anyway
            if raw.startswith("```"):
                raw = raw.strip("`")
                raw = raw.split("json", 1)[-1] if raw.lower().startswith("json") else raw
            parsed = json.loads(raw)
            score = int(parsed["score"])
            if not (1 <= score <= 5):
                raise ValueError(f"Score out of range: {score}")
            return JudgeResult(score=score, justification=parsed.get("justification", ""))
        except Exception as exc:
            last_error = str(exc)
            logger.warning(f"Judge attempt {attempt + 1} failed: {last_error}")

    return JudgeResult(score=None, justification="", error=f"Judge failed after retries: {last_error}")