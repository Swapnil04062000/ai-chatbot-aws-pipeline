import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage

from pydantic import BaseModel

# Import our dedicated production telemetry engine
from otel_metrics import chatbot_telemetry
from opentelemetry import trace

# LangGraph agent (replaces the direct client.chat.completions.create call)
from langgraph_version.graph import run_agent, ENABLE_TOOLS

import uvicorn
from contextlib import asynccontextmanager

load_dotenv()

logger = logging.getLogger("chatbot_production")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Modern FastAPI lifespan context manager.
    Handles non-blocking startup diagnostics, telemetry background tasks,
    and graceful shutdown. The LLM client itself now lives inside the
    LangGraph agent (agent/graph.py) and is lazily built on first use, so
    there's no longer a raw client to construct here -- we just verify the
    API key is present up front so "demo mode" is a clean, immediate check.
    """
    global api_key_present
    logger.info("Starting up FastAPI application lifespan...")

    api_key_present = bool(os.getenv("OPENAI_API_KEY"))
    if api_key_present:
        logger.info(f"LangGraph agent ready (tools_enabled={ENABLE_TOOLS}).")
    else:
        logger.warning("No OpenAI API key found. Running in demo mode.")

    # Bootstrap OTel telemetry pipeline and hardware metrics background loop
    try:
        chatbot_telemetry.initialize()
        chatbot_telemetry.start_background_monitoring()
        logger.info("System hardware monitor task & telemetry started.")
    except Exception as otel_err:
        logger.error(f"Failed to start telemetry: {otel_err}")

    yield  # Application runs here

    logger.info("Shutting down application lifespan...")


app = FastAPI(title="AI Chat - LangGraph Agent + OTel", lifespan=lifespan)

api_key_present: bool = False


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]


def _to_langchain_messages(messages: list[Message]) -> list[BaseMessage]:
    """
    Convert the API's plain role/content messages into LangChain message
    objects the graph expects. Unrecognized roles fall back to HumanMessage
    rather than raising, so a malformed role never crashes a request --
    consistent with the original code's forgiving `str(m.role)` handling.
    """
    role_map = {
        "user": HumanMessage,
        "assistant": AIMessage,
        "system": SystemMessage,
    }
    converted: list[BaseMessage] = []
    for m in messages:
        role = str(m.role).strip().lower()
        cls = role_map.get(role, HumanMessage)
        converted.append(cls(content=str(m.content)))
    return converted


@app.post("/api/chat")
async def chat(request: ChatRequest, x_session_id: str = Header(default="unknown")):
    if not request.messages:
        raise HTTPException(status_code=400, detail="Messages are required")

    tracer = chatbot_telemetry.tracer
    if tracer is None:
        tracer = trace.get_tracer(__name__)

    # Trace the full HTTP endpoint lifecycle -- unchanged from before
    with tracer.start_as_current_span("http_chat_endpoint") as parent_span:
        parent_span.set_attribute("http.route", "/api/chat")
        parent_span.set_attribute("session_id", x_session_id)

        if not api_key_present:
            last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
            return {"message": f"Demo Mode response. You said: {last_user}"}

        target_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        try:
            lc_messages = _to_langchain_messages(request.messages)

            # Same telemetry context manager as before -- the agent swap is
            # transparent to the OTel layer, since we still hand it a
            # token_holder dict with "in"/"out" keys to populate.
            async with chatbot_telemetry.trace_chat_call(target_model, session_id=x_session_id) as token_holder:
                result = await run_agent(lc_messages, session_id=x_session_id)

                if token_holder is not None:
                    token_holder["in"] = result["input_tokens"]
                    token_holder["out"] = result["output_tokens"]

                return {"message": result["reply"]}

        except Exception as exc:
            parent_span.record_exception(exc)
            parent_span.set_status(trace.StatusCode.ERROR, str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/telemetry/stats")
async def get_telemetry_stats():
    """Diagnostic fallback endpoint to inspect current metrics out of memory"""
    return {
        "success": True,
        "chatbot_stats": chatbot_telemetry.get_aggregated_stats()
    }


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)