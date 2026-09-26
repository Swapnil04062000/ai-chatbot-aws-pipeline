"""
Thin async client for the chatbot API under test.
Mirrors exactly what the real frontend sends (messages array + X-Session-Id
header) so the eval suite exercises the same code path a real user hits --
no special "test mode" endpoint, no mocking of the app itself.
"""

import time
import uuid
import httpx
from dataclasses import dataclass
from typing import Optional

from eval_suite.config import TARGET


@dataclass
class ChatResult:
    ok: bool
    status_code: Optional[int]
    message: str
    latency_ms: float
    error: Optional[str] = None
    session_id: str = ""


async def call_chat(user_input: str, session_id: Optional[str] = None) -> ChatResult:
    """
    Fire a single chat request against the live/local app, exactly as the
    real frontend would. Returns a ChatResult capturing success, latency,
    and the raw response text (or error).
    """
    session_id = session_id or f"eval-{uuid.uuid4()}"
    url = f"{TARGET.base_url}{TARGET.chat_endpoint}"
    payload = {"messages": [{"role": "user", "content": user_input}]}
    headers = {"Content-Type": "application/json", "X-Session-Id": session_id}

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=TARGET.timeout_sec) as client:
            resp = await client.post(url, json=payload, headers=headers)
        latency_ms = (time.perf_counter() - start) * 1000

        if resp.status_code != 200:
            return ChatResult(
                ok=False,
                status_code=resp.status_code,
                message="",
                latency_ms=latency_ms,
                error=f"HTTP {resp.status_code}: {resp.text[:300]}",
                session_id=session_id,
            )

        data = resp.json()
        message = data.get("message", "")
        return ChatResult(
            ok=True,
            status_code=resp.status_code,
            message=message,
            latency_ms=latency_ms,
            session_id=session_id,
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return ChatResult(
            ok=False,
            status_code=None,
            message="",
            latency_ms=latency_ms,
            error=f"{type(exc).__name__}: {exc}",
            session_id=session_id,
        )