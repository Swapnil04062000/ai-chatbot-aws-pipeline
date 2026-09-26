"""
Tools available to the agent.

Kept deliberately small and dependency-free (no external APIs) so the
agent is genuinely testable offline and doesn't introduce new network
calls / failure modes beyond the LLM itself. Add more tools here as
needed -- each just needs the `@tool` decorator and a clear docstring
(the docstring IS the tool description the LLM sees, so keep it precise).
"""

import ast
import operator
import logging
from datetime import datetime, timezone

from langchain_core.tools import tool

logger = logging.getLogger("chatbot_agent.tools")


# ---------------------------------------------------------------------------
# Safe arithmetic evaluator (no eval()/exec() -- parses a restricted AST so
# the tool can't be used to run arbitrary Python even if the model is
# adversarially prompted into passing something malicious as "an expression").
# ---------------------------------------------------------------------------
_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Only numeric constants are allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_safe_eval(node.operand))
    raise ValueError(f"Unsupported expression element: {type(node).__name__}")


@tool
def calculator(expression: str) -> str:
    """
    Evaluate a basic arithmetic expression and return the numeric result.
    Supports +, -, *, /, **, %, parentheses, and unary minus/plus.
    Use this for any math the user asks about instead of computing it
    yourself, since arithmetic mistakes are otherwise easy to make.
    Example input: "(15 + 9) * 2 - 4"
    """
    try:
        parsed = ast.parse(expression, mode="eval").body
        result = _safe_eval(parsed)
        return str(result)
    except Exception as exc:
        return f"Error: could not evaluate '{expression}' -- {exc}"


@tool
def get_current_datetime() -> str:
    """
    Return the current date and time in UTC, ISO 8601 format.
    Use this whenever the user asks what the current date/time is, or asks
    you to reason about "today", "now", "this week", etc.
    """
    return datetime.now(timezone.utc).isoformat()


@tool
def get_chatbot_operating_stats() -> str:
    """
    Return this chatbot's own live operating statistics -- total requests
    served, average/min/max response latency in milliseconds, error rate,
    and total input/output tokens used so far in this process's lifetime.
    Use this only if the user explicitly asks about the chatbot's own
    performance, usage, or health (e.g. "how many messages have you
    answered", "what's your average response time").
    """
    try:
        # Local import avoids a circular import at module load time, since
        # otel_metrics also gets imported by main.py.
        from otel_metrics import chatbot_telemetry
        stats = chatbot_telemetry.get_aggregated_stats()
        return str(stats)
    except Exception as exc:
        logger.warning(f"get_chatbot_operating_stats tool failed: {exc}")
        return "Stats are currently unavailable."


TOOLS = [calculator, get_current_datetime, get_chatbot_operating_stats]