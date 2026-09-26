"""
LangGraph agent definition.

Two graph shapes are supported, controlled by AGENT_ENABLE_TOOLS:

  ENABLE_TOOLS=true  (default) -- a standard ReAct-style loop:

      START -> agent -> (has tool_calls?) -> tools -> agent -> ... -> END
                      -> (no tool_calls)  -----------------------> END

  ENABLE_TOOLS=false -- a single-node graph (agent -> END). This exists as
  a safety net: this app's model is served through a Bedrock-compatible
  OpenAI proxy, and whether that specific gateway supports OpenAI-style
  function/tool calling for this model hasn't been verified. If tool
  calls error out or are silently ignored by the gateway, set
  AGENT_ENABLE_TOOLS=false to run a plain conversational graph instead --
  still a real LangGraph agent, just without tool use.

Token accounting: `_agent_node` reads `response.usage_metadata` (LangChain's
standardized usage field, populated from whatever the underlying OpenAI-
compatible API returns) and accumulates it into graph state across every
LLM call in a run, so a multi-step tool-calling turn still reports accurate
total input/output tokens for that single user turn.
"""

import os
import logging
from typing import Literal, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from langgraph_version.state import AgentState
from langgraph_version.tools import TOOLS

logger = logging.getLogger("chatbot_agent.graph")

ENABLE_TOOLS = os.getenv("AGENT_ENABLE_TOOLS", "true").lower() == "true"
RECURSION_LIMIT = int(os.getenv("AGENT_RECURSION_LIMIT", "8"))

SYSTEM_PROMPT = (
    "You are a helpful AI assistant. "
    + (
        "You have access to tools -- use them when they would genuinely "
        "help (arithmetic, the current date/time, or your own operating "
        "stats if explicitly asked). Otherwise, answer directly without "
        "calling a tool."
        if ENABLE_TOOLS
        else "Answer clearly and directly."
    )
)

_llm: Optional[ChatOpenAI] = None


def _get_llm() -> ChatOpenAI:
    """Lazily-constructed, cached LLM client -- built once, reused across
    every graph invocation (mirrors how the original code cached a single
    module-level `client`)."""
    global _llm
    if _llm is None:
        base = ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.3")),
        )
        _llm = base.bind_tools(TOOLS) if ENABLE_TOOLS else base
    return _llm


async def _agent_node(state: AgentState) -> dict:
    llm = _get_llm()
    messages = list(state["messages"])
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages

    # Log incoming tool execution results when resuming after tool execution
    recent_tool_messages = []
    for m in reversed(messages):
        if isinstance(m, ToolMessage):
            recent_tool_messages.append(m)
        else:
            break
    for m in reversed(recent_tool_messages):
        tool_name = getattr(m, "name", "tool")
        print(f"\n[AGENT] 📥 Tool Result ('{tool_name}'): {m.content}")
        logger.info(f"Tool Result ('{tool_name}'): {m.content}")

    response: AIMessage = await llm.ainvoke(messages)

    usage = getattr(response, "usage_metadata", None) or {}
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0

    return {
        "messages": [response],
        "input_tokens": state.get("input_tokens", 0) + input_tokens,
        "output_tokens": state.get("output_tokens", 0) + output_tokens,
    }


def _should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            tool_name = tc.get("name", "unknown")
            tool_args = tc.get("args", {})
            print(f"\n[AGENT] 🛠️  Calling Tool: '{tool_name}' | Arguments: {tool_args}")
            logger.info(f"Agent invoking tool: '{tool_name}' with args: {tool_args}")
        return "tools"
    return END


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("agent", _agent_node)
    graph.set_entry_point("agent")

    if ENABLE_TOOLS:
        graph.add_node("tools", ToolNode(TOOLS))
        graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
        graph.add_edge("tools", "agent")
    else:
        graph.add_edge("agent", END)

    return graph.compile()


_compiled_graph = None


def get_agent():
    """Singleton accessor for the compiled graph."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
        logger.info(f"LangGraph agent compiled (tools_enabled={ENABLE_TOOLS}, tools={[t.name for t in TOOLS] if ENABLE_TOOLS else []})")
    return _compiled_graph


async def run_agent(messages, session_id: str = "unknown") -> dict:
    """
    Convenience entrypoint used by main.py. Takes a list of LangChain
    BaseMessage objects (already converted from the API's request format),
    runs the graph to completion, and returns a plain dict:
        {"reply": str, "input_tokens": int, "output_tokens": int}
    """
    agent = get_agent()
    result = await agent.ainvoke(
        {"messages": messages, "input_tokens": 0, "output_tokens": 0},
        config={"recursion_limit": RECURSION_LIMIT, "run_name": f"chat-{session_id}"},
    )
    final_message = result["messages"][-1]
    reply = final_message.content if isinstance(final_message.content, str) else str(final_message.content)
    return {
        "reply": reply,
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
    }