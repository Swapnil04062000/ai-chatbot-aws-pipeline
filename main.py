import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

# Import our dedicated production telemetry engine
from otel_metrics import chatbot_telemetry
from opentelemetry import trace
from google import genai

import uvicorn
from contextlib import asynccontextmanager

load_dotenv()

# print("")

# Bootstrap OTel telemetry pipeline on startup
# chatbot_telemetry.initialize()
logger = logging.getLogger("chatbot_production")

# app = FastAPI(title="AI Chat - Production OTel")
STATIC_DIR = Path(__file__).parent / "static"

# client = None
# if os.getenv("OPENAI_API_KEY"):
#     client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),base_url=os.getenv("OPENAI_BASE_URL"))
#     logger.info("OpenAI Client successfully loaded.")
# else:
#     logger.warning("No OpenAI API key found. Running in demo mode.")


# models = client.models.list()
# print("--- Models Available to Your Bedrock Key ---")
# for model in models:
#     print(f"- {model.id}")


# for model_obj in client.models.list():
#     m_id = model_obj.id
#     try:
#         res = client.chat.completions.create(
#             model=m_id,
#             messages=[{"role": "user", "content": "ping"}],
#             max_tokens=5
#         )
#         print(f" [SUCCESS] -> {m_id}")
#         break  # Stop at the first working model
#     except Exception as e:
#         print(f" [BLOCKED] -> {m_id}: {str(e).splitlines()}")

# client = None
# try:
#     if os.getenv("GOOGLE_GENAI_USE_VERTEXAI") == "TRUE" or os.getenv("GOOGLE_CLOUD_PROJECT"):
#         client = genai.Client()
#         logger.info("Vertex AI Gemini Client successfully loaded via google-genai SDK.")
#     else:
#         logger.warning("Google Cloud Project configuration not found. Running in demo mode.")
# except Exception as e:
#     logger.error(f"Failed to initialize Vertex AI client: {e}")



# @app.on_event("startup")
# async def startup_event():
#     # Start async hardware metrics scraper loop
#     chatbot_telemetry.start_background_monitoring()
#     logger.info("System hardware monitor task started.")



@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Modern FastAPI lifespan context manager.
    Handles non-blocking startup diagnostics, telemetry background tasks, and graceful shutdown.
    """
    global client
    logger.info("Starting up FastAPI application lifespan...")

    # 1. Safely initialize OpenAI/Bedrock client
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    if api_key:
        try:
            client = OpenAI(api_key=api_key, base_url=base_url)
            logger.info("OpenAI Client successfully loaded.")
           
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
    else:
        logger.warning("No OpenAI API key found. Running in demo mode.")

    # 2. Bootstrap OTel telemetry pipeline and hardware metrics background loop
    try:
        chatbot_telemetry.initialize()
        chatbot_telemetry.start_background_monitoring()
        logger.info("System hardware monitor task & telemetry started.")
    except Exception as otel_err:
        logger.error(f"Failed to start telemetry: {otel_err}")

    yield  # Application runs here

    # 3. Shutdown cleanup logic (if any)
    logger.info("Shutting down application lifespan...")


# Pass the lifespan handler to FastAPI
app = FastAPI(title="AI Chat - Production OTel", lifespan=lifespan)


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]


@app.post("/api/chat")
async def chat(request: ChatRequest, x_session_id: str = Header(default="unknown")):
    if not request.messages:
        raise HTTPException(status_code=400, detail="Messages are required")

    tracer = chatbot_telemetry.tracer

    if tracer is None:
        tracer = trace.get_tracer(__name__)
    
    # Trace the full HTTP endpoint lifecycle
    with tracer.start_as_current_span("http_chat_endpoint") as parent_span:
        parent_span.set_attribute("http.route", "/api/chat")
        parent_span.set_attribute("session_id", x_session_id)

        if client is None:
            last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
            return {"message": f"Demo Mode response. You said: {last_user}"}

        target_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        ######################

        # try:
        #     # Use our clean asynchronous context manager for the GenAI downstream call
        #     async with chatbot_telemetry.trace_chat_call(target_model) as token_holder:
        #         response = client.chat.completions.create(
        #             model=target_model,
        #             # messages=[{"role": m.role, "content": m.content} for m in request.messages],
        #             messages=[{"role": str(m.role), "content": str(m.content)} for m in request.messages],
        #         )
                
        #         # Pass usage metadata back to the telemetry context manager
        #         if response.usage and token_holder:
        #             token_holder["in"] = response.usage.prompt_tokens
        #             token_holder["out"] = response.usage.completion_tokens

        #         return {"message": response.choices[0].message.content or ""}

        ##########################

        try:
            # Convert Pydantic objects explicitly into clean Python dictionary literals
            # This ensures that any model serialization artifacts are stripped out
            openai_messages = [
                {
                    "role": str(m.role).strip().lower(), 
                    "content": str(m.content)
                } 
                for m in request.messages
            ]

            # Use our clean asynchronous context manager for the GenAI downstream call
            async with chatbot_telemetry.trace_chat_call(target_model, session_id=x_session_id) as token_holder:
                response = client.chat.completions.create(
                    model=target_model,
                    messages=openai_messages,  # Pass the sanitized list directly
                )
                
                # Pass usage metadata back to the telemetry context manager
                if response.usage and token_holder:
                    token_holder["in"] = response.usage.prompt_tokens
                    token_holder["out"] = response.usage.completion_tokens

                return {"message": response.choices[0].message.content or ""}


                # target_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

                # formatted_contents = "\n".join([f"{m.role}: {m.content}" for m in request.messages])
                # # Call Vertex AI Gemini model
                # response = client.models.generate_content(
                #     model=target_model,
                #     contents= formatted_contents,
                # )
                
                # # Extract token usage metadata from Gemini response structure
                # if response.usage_metadata and token_holder:
                #     prompt_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                #     candidates_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
                    
                #     token_holder["in"] = prompt_tokens
                #     token_holder["out"] = candidates_tokens

                # return {"message": response.text or ""}
                
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

print("adding force trigger")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")




if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)