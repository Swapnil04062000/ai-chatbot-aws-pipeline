# Chatbot → LangGraph Agent Conversion

## What changed

The chatbot went from a single direct call (`client.chat.completions.create`)
to a real **LangGraph agent**: a graph with an `agent` node (calls the LLM)
and a `tools` node (executes any tool the LLM decides to call), looping
between them until the LLM produces a final answer with no more tool calls.

```
START → agent ─┬─(no tool_calls)──→ END
                └─(has tool_calls)─→ tools → agent → ... → END
```

This is the standard "ReAct-style" agent pattern -- reason, act (call a
tool), observe the result, repeat.

## Files added

```
agent/
  __init__.py
  state.py    -- graph state schema (messages + running token counts)
  tools.py    -- calculator, current datetime, self-introspection into
                 the app's own telemetry stats
  graph.py    -- the graph itself: agent node, tool node, routing logic,
                 the run_agent() entrypoint main.py calls
main.py       -- updated: replaces the direct OpenAI call with run_agent(),
                 everything else (routes, telemetry wiring, demo mode,
                 static file serving) is unchanged
```

## What's preserved exactly as before

- **API contract** -- `/api/chat` still takes `{"messages": [...]}` and
  returns `{"message": "..."}`. The frontend needs zero changes.
- **OTel/X-Ray tracing** -- `chatbot_telemetry.trace_chat_call(model,
  session_id=...)` is called exactly the same way, with the same
  `token_holder["in"]/["out"]` contract. Your `session_id` annotation
  filtering in X-Ray keeps working unchanged.
- **Demo mode** -- if `OPENAI_API_KEY` isn't set, it still returns a demo
  response without touching the agent at all.
- **Token accounting** -- now more accurate than before, actually: a
  tool-calling turn makes 2+ LLM calls (one to decide to use a tool, one
  to respond after seeing the result), and `agent/graph.py` sums token
  usage across *all* of them for that turn, so your telemetry reflects
  the true cost of a turn, not just the last LLM call in it.

## Important caveat: tool-calling support is unverified for your model

Your model is served through a Bedrock-compatible OpenAI proxy
(`qwen.qwen3-next-80b-a3b-instruct` via `OPENAI_BASE_URL`). Whether that
specific gateway correctly implements OpenAI's `tools`/function-calling
API surface for this model **hasn't been tested against your real
endpoint** -- I built and tested the graph logic itself with a fake LLM
(proving the routing, tool execution, and token accounting all work
correctly), but not against your actual Bedrock proxy, since I don't have
access to your credentials/environment from here.

**Test this first, before relying on tool use in production:**
```bash
# with a real OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL set
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What is 47 times 89?"}]}'
```
If you get `"4183"` (or similar correct arithmetic), tool calling works
end to end against your real gateway. If you get an error, a garbled
response, or the model just tries to compute it in its head (and gets it
wrong), the gateway likely doesn't support tool calling for this model.

**Safety net -- if tool calling doesn't work on your gateway:**
```bash
export AGENT_ENABLE_TOOLS=false
```
This switches to a single-node graph (`agent → END`, no tools node at
all) -- still a real LangGraph agent, just without tool use. Nothing else
changes; the API contract, telemetry, and demo mode all behave the same.

## New dependencies

Add to `requirements.txt`:
```
langgraph>=1.2.0
langchain-core>=1.6.0
langchain-openai>=1.6.0
```

## New environment variables (all optional, sensible defaults)

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_ENABLE_TOOLS` | `true` | Set `false` to fall back to a plain single-node graph if tool calling isn't supported by your gateway |
| `AGENT_RECURSION_LIMIT` | `8` | Safety cap on agent↔tools loop iterations per turn, prevents a runaway loop from an LLM that keeps calling tools indefinitely |
| `OPENAI_TEMPERATURE` | `0.3` | Sampling temperature for the agent's LLM |

## What I actually tested (not just wrote)

Since I don't have access to your live environment, I validated the parts
I *can* validate from here, with a fake LLM standing in for the real
model call (everything else -- the graph, the real tool functions, the
FastAPI app, the telemetry wiring -- is genuinely exercised, not mocked):

1. **Full tool-calling loop** -- agent requests the calculator tool, the
   REAL `calculator()` function executes (`12 * 7` → `84`, computed by an
   actual restricted-AST evaluator, not hardcoded), the result correctly
   feeds back into the second LLM call, and token usage accumulates
   correctly across both calls (120 input / 18 output tokens summed).
2. **Direct-answer path** -- confirms a response with no tool calls goes
   straight to `END` with exactly one LLM call, no wasted tool-node hop.
3. **`AGENT_ENABLE_TOOLS=false` fallback** -- confirms the single-node
   graph builds and runs correctly as a safety net.
4. **Full FastAPI integration** -- a real `TestClient` request through
   `/api/chat`, through the real telemetry context manager, through the
   real LangGraph agent, with the real tool executing -- confirmed the
   session ID and accumulated token counts land correctly in the
   telemetry layer, and the response shape matches what your frontend
   expects.
5. **Demo mode** -- confirmed it still short-circuits correctly with no
   API key set, without invoking the agent.

What I could **not** test from here: an actual call against your live
Bedrock-compatible gateway, since I don't have your credentials or
network access to it. That's the one thing to verify yourself first,
per the caveat above, before trusting tool use in production.

## Extending the agent further (natural next steps)

- **More tools** -- add to `agent/tools.py`; each just needs `@tool` and
  a clear docstring (the docstring is literally what the LLM reads to
  decide when to use it).
- **Streaming** -- LangGraph supports `.astream()` for token-by-token
  streaming; the current `run_agent()` uses `.ainvoke()` (waits for the
  full turn) to keep the change minimal and match your existing
  single-shot API contract. Streaming would need a corresponding frontend
  change (SSE or WebSocket) to actually display incrementally.
- **Persistent memory across turns** -- right now each `/api/chat` call
  passes the full message history from the frontend (as before). LangGraph
  supports checkpointing (e.g. via a `MemorySaver` or a Redis-backed
  checkpointer) if you want the graph itself to own conversation state
  server-side instead.