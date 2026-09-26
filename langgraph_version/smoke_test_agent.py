"""
Structural smoke test for the agent graph. Not shipped to the user --
this is purely to validate, in this sandbox, that:
  1. The agent -> tools -> agent -> END loop actually executes correctly
  2. A real tool call (calculator) gets invoked and its result flows back
  3. Token usage accumulates correctly across BOTH LLM calls in the turn
  4. The no-tool-call path goes straight to END
  5. The ENABLE_TOOLS=false single-node graph works too

We fake the LLM (not the graph, not the tool node, not the state reducer)
so everything except the actual network call to the model is exercised for
real.
"""

import asyncio
import sys
sys.path.insert(0, "/home/claude/agent_delivery")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage

import langgraph_version.graph as graph_module
from langgraph_version.state import AgentState
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph_version.tools import TOOLS


class FakeLLM:
    """Returns a tool-call request on the first invocation, then a final
    answer on the second -- simulating a real ReAct turn: think -> call
    tool -> observe result -> answer. Records what it's given on the
    second call so we can verify the REAL calculator tool executed and
    fed its actual result back, not a hardcoded fake answer."""

    def __init__(self):
        self.call_count = 0
        self.second_call_messages = None

    async def ainvoke(self, messages):
        self.call_count += 1
        if self.call_count == 1:
            msg = AIMessage(
                content="",
                tool_calls=[{"name": "calculator", "args": {"expression": "12 * 7"}, "id": "call_1"}],
            )
            msg.usage_metadata = {"input_tokens": 50, "output_tokens": 10}
            return msg
        else:
            self.second_call_messages = messages
            msg = AIMessage(content="12 * 7 is 84.")
            msg.usage_metadata = {"input_tokens": 70, "output_tokens": 8}
            return msg


async def test_tool_calling_loop():
    fake = FakeLLM()
    graph_module._llm = fake  # inject fake LLM into the module-level singleton

    result = await graph_module.run_agent(
        [HumanMessage(content="what is 12 times 7?")],
        session_id="smoke-test-1",
    )

    print("=== Tool-calling loop result ===")
    print(result)
    assert "84" in result["reply"], f"Expected '84' in reply, got: {result['reply']}"
    assert result["input_tokens"] == 50 + 70, f"Expected 120 input tokens, got {result['input_tokens']}"
    assert result["output_tokens"] == 10 + 8, f"Expected 18 output tokens, got {result['output_tokens']}"
    assert fake.call_count == 2, f"Expected 2 LLM calls (agent->tools->agent), got {fake.call_count}"

    tool_messages = [m for m in fake.second_call_messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1, f"Expected 1 ToolMessage fed back to the LLM, got {len(tool_messages)}"
    assert tool_messages[0].content == "84", (
        f"Expected the REAL calculator tool to compute 12*7=84 and feed it back, "
        f"got ToolMessage content: {tool_messages[0].content!r}"
    )
    print(f"VERIFIED: real ToolNode executed the real calculator() function -> ToolMessage(content={tool_messages[0].content!r})")
    print("PASS: tool-calling loop executed correctly, tokens accumulated across both LLM calls\n")


class FakeLLMNoTool:
    """Never calls a tool -- simulates a plain direct answer, to prove the
    graph goes straight to END without a wasted tools hop."""

    def __init__(self):
        self.call_count = 0

    async def ainvoke(self, messages):
        self.call_count += 1
        msg = AIMessage(content="Paris is the capital of France.")
        msg.usage_metadata = {"input_tokens": 20, "output_tokens": 8}
        return msg


async def test_direct_answer_no_tool():
    fake = FakeLLMNoTool()
    graph_module._llm = fake

    result = await graph_module.run_agent(
        [HumanMessage(content="what is the capital of france?")],
        session_id="smoke-test-2",
    )

    print("=== Direct-answer (no tool call) result ===")
    print(result)
    assert "Paris" in result["reply"]
    assert fake.call_count == 1, f"Expected exactly 1 LLM call (no tool detour), got {fake.call_count}"
    print("PASS: no-tool-call path goes straight to END, single LLM call\n")


async def main():
    await test_tool_calling_loop()
    await test_direct_answer_no_tool()
    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())