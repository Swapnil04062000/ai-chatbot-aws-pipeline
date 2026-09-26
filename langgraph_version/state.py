"""
Graph state schema.

`messages` uses LangGraph's built-in `add_messages` reducer, which APPENDS
new messages to the list rather than overwriting it -- essential for a
tool-calling loop, where the agent node adds an AIMessage (possibly with
tool_calls), the tools node adds ToolMessage results, and the agent node
runs again with the full accumulated history.

`input_tokens` / `output_tokens` accumulate across every LLM call made
during a single graph run (a tool-calling loop can invoke the LLM more
than once per user turn), so the final totals handed back to the existing
OTel telemetry layer are accurate for the whole turn, not just the last
call.
"""

from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    input_tokens: int
    output_tokens: int