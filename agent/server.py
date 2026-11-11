"""
Custom LLM proxy server for ElevenLabs.

Translates between ElevenLabs' OpenAI-compatible request format
and LangGraph's streaming output. Follows the FDE team's blog
pattern: receive messages → run agent → stream filtered SSE chunks.

Run:
    uvicorn agent.server:app --reload --port 8000

Reference:
    https://elevenlabs.io/blog/practical-guide-open-source-agent-frameworks-and-elevenagents
"""

import json
import os
import time
import uuid

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from pydantic import BaseModel

from agent.graph import build_graph
from agent.logging_config import get_logger
from agent.prompts.loader import load_system_prompt, get_latest_version

logger = get_logger(__name__)

# Build graph without checkpointer — ElevenLabs is stateless and sends
# full message history with every request, so no server-side persistence needed.
_compiled_graph = build_graph()


# --- Circuit breaker (graceful degradation) ---
#
# Tracks consecutive LangGraph failures. When threshold is exceeded,
# proxy switches to Level 1 degradation: direct OpenAI call with
# system prompt, bypassing the graph.
#
# Levels:
#   0 — Healthy: full LangGraph agent
#   1 — Degraded: direct OpenAI LLM call (bypass graph)
#   2 — Down: ElevenLabs falls back to its default agent
#       (configured via backup_llm_config in deploy script)
#

_CB_THRESHOLD = 3          # consecutive failures before opening circuit
_CB_COOLDOWN_SECONDS = 60  # seconds before retrying LangGraph
_cb_failures = 0
_cb_opened_at: float | None = None


def _cb_record_success() -> None:
    """Reset circuit breaker on successful graph execution."""
    global _cb_failures, _cb_opened_at
    if _cb_failures > 0:
        logger.info(f"Circuit breaker reset after {_cb_failures} failures")
    _cb_failures = 0
    _cb_opened_at = None


def _cb_record_failure() -> None:
    """Record a graph failure. Opens circuit after threshold."""
    global _cb_failures, _cb_opened_at
    _cb_failures += 1
    if _cb_failures >= _CB_THRESHOLD and _cb_opened_at is None:
        _cb_opened_at = time.time()
        logger.warning(f"Circuit breaker OPEN — {_cb_failures} consecutive failures, switching to Level 1")


def _cb_is_open() -> bool:
    """Check if circuit breaker is open (Level 1 degradation active).

    Auto-closes after cooldown to allow retrying LangGraph.
    """
    global _cb_failures, _cb_opened_at
    if _cb_failures < _CB_THRESHOLD:
        return False
    # Allow retry after cooldown
    if _cb_opened_at and (time.time() - _cb_opened_at) > _CB_COOLDOWN_SECONDS:
        logger.info("Circuit breaker cooldown expired — retrying LangGraph")
        _cb_failures = 0
        _cb_opened_at = None
        return False
    return True


def _cb_status() -> dict:
    """Return circuit breaker state for health endpoint."""
    if _cb_is_open():
        return {"level": 1, "label": "degraded", "failures": _cb_failures}
    return {"level": 0, "label": "healthy", "failures": _cb_failures}


from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute"],  # Global: 60 requests/min per IP
)

app = FastAPI(
    title="Citizen Services Custom LLM",
    version="0.1.0",
    description="LangGraph agent exposed as OpenAI-compatible Custom LLM for ElevenLabs.",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- Security middleware ---

# CORS — restrict to ElevenLabs and local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://elevenlabs.io",
        "https://api.elevenlabs.io",
        "http://localhost:3000",
        "http://localhost:8080",
    ],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.middleware("http")
async def security_headers(request, call_next):
    """Add security headers to every response (SOC2 / OWASP best practices)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# Mount government API routers on the same server.
# Single port = single ngrok tunnel = ElevenLabs can reach both
# Custom LLM (/v1/chat/completions) and Government API (/auth, /appointments, etc.)
# Because this app is public, citizen-data routers require X-Internal-Key
# (sent by LangGraph nodes) and erasure requires X-Admin-Key — see api/security.py.
from api.auth import router as auth_router
from api.handoff import router as handoff_router, guest_router
from api.services import (
    router as services_router,
    public_router as services_public_router,
    admin_router as services_admin_router,
)
from api.twilio_webhook import router as twilio_router
from api.models import init_db
from api.crypto import keyed_hash
from agent import session_token

app.include_router(auth_router)
app.include_router(handoff_router)
app.include_router(guest_router)
app.include_router(services_router)
app.include_router(services_public_router)
app.include_router(services_admin_router)
app.include_router(twilio_router)

# Ensure DB tables exist on startup
# Note: using on_event for simplicity since app already defined.
# Main api/server.py uses lifespan pattern.
@app.on_event("startup")
def _init_gov_db():
    init_db()
    if not os.getenv("INTERNAL_API_KEY"):
        logger.warning(
            "INTERNAL_API_KEY is not set — government API will refuse every "
            "citizen-data request, so service nodes cannot reach it"
        )
    if not os.getenv("PLATFORM_WEBHOOK_SECRET"):
        logger.warning(
            "PLATFORM_WEBHOOK_SECRET is not set — /auth routes will refuse every "
            "request, so callers cannot authenticate"
        )
    for check in (lambda: keyed_hash("startup-check"), lambda: session_token.issue(0, "startup-check")):
        try:
            check()
        except RuntimeError as e:
            logger.error(f"{e} — identity verification will fail until it is set")


# --- Request model (OpenAI Chat Completions format) ---

class Message(BaseModel):
    role: str
    content: str | None = None

class ChatCompletionRequest(BaseModel):
    messages: list[Message]
    model: str = "langgraph-citizen-agent"
    temperature: float | None = 0.7
    max_tokens: int | None = None
    stream: bool | None = True
    tools: list[dict] | None = None  # System tools sent by ElevenLabs
    elevenlabs_extra_body: dict | None = None


# --- SSE helper (from FDE blog) ---

def sse_chunk(response_id: str, delta: dict, finish_reason: str | None = None) -> str:
    """Format a single SSE chunk in OpenAI chat completion format."""
    import time
    payload = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "langgraph-citizen-agent",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _stream_text_as_chunks(response_id: str, text: str):
    """Break text into word-level SSE chunks for natural TTS streaming.

    ElevenLabs TTS works best with small, incremental chunks —
    just like OpenAI streams token by token. Sending one giant
    chunk causes TTS buffer issues and garbled speech.
    """
    words = text.split(" ")
    for i, word in enumerate(words):
        # Add space before word (except first)
        content = f" {word}" if i > 0 else word
        yield sse_chunk(response_id, {"content": content})


# --- Request helpers ---

def _extract_conversation_id(request: ChatCompletionRequest) -> str:
    """Extract conversation ID from ElevenLabs extra body or generate one.

    Used for logging only — ElevenLabs is stateless and sends full
    message history with every request, so no server-side state needed.
    """
    if request.elevenlabs_extra_body:
        conv_id = request.elevenlabs_extra_body.get("conversation_id")
        if conv_id:
            return conv_id
    return f"local-{uuid.uuid4().hex[:12]}"


def _extract_auth_state(request: ChatCompletionRequest) -> tuple[str, dict | None]:
    """Extract auth status and citizen profile from extra body."""
    if request.elevenlabs_extra_body:
        auth_status = request.elevenlabs_extra_body.get("auth_status", "unauthenticated")
        profile = request.elevenlabs_extra_body.get("citizen_profile")
        return auth_status, profile
    return "unauthenticated", None


def _extract_language(request: ChatCompletionRequest) -> str:
    """Extract conversation language.

    Priority:
    1. ElevenLabs extra_body (set by platform language_detection tool)
    2. Default to 'en' (English is primary demo language)

    We do NOT try to detect language from message text — short sentences
    get misclassified. ElevenLabs' native language_detection system tool
    handles switching when needed.
    """
    if request.elevenlabs_extra_body:
        return request.elevenlabs_extra_body.get("language", "en")
    return "en"




# --- Level 1 fallback (direct OpenAI call, bypass LangGraph) ---

async def _level1_fallback(messages: list, language: str):
    """Level 1 degradation: direct OpenAI call with system prompt.

    Bypasses LangGraph graph — no intent routing, no tool chaining,
    no deterministic flows. Just a simple LLM conversation with the
    system prompt for basic citizen guidance.
    """
    from openai import AsyncOpenAI

    system_prompt = load_system_prompt(language=language, version=get_latest_version())
    openai_messages = [{"role": "system", "content": system_prompt}]

    for m in messages:
        if isinstance(m, HumanMessage):
            openai_messages.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage):
            openai_messages.append({"role": "assistant", "content": m.content or ""})

    try:
        client = AsyncOpenAI()
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=openai_messages,
            stream=True,
            temperature=0.7,
            max_tokens=512,
        )
        async for chunk in response:
            delta_content = chunk.choices[0].delta.content
            if delta_content:
                yield delta_content

    except Exception as e:
        logger.error(f"Level 1 fallback error: {e}")
        if language == "en":
            yield "We are experiencing technical difficulties. Please try again later."
        else:
            yield "Teknik bir sorun yasiyoruz. Lutfen daha sonra tekrar deneyin."


# --- Endpoint ---

@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """OpenAI-compatible chat completions endpoint for ElevenLabs Custom LLM."""
    conversation_id = _extract_conversation_id(request)
    auth_status, citizen_profile = _extract_auth_state(request)
    language = _extract_language(request)

    # Language switching is handled by ElevenLabs' native language_detection
    # system tool — we don't detect or trigger switches ourselves.

    logger.info(f"Custom LLM request | conv={conversation_id} | messages={len(request.messages)} | lang={language}")
    if request.elevenlabs_extra_body:
        logger.info(f"Extra body: {request.elevenlabs_extra_body}")
    # Log system prompt and extract dynamic variables
    system_prompt_content = ""
    for msg in request.messages[:2]:
        if msg.role == "system":
            system_prompt_content = msg.content or ""
            logger.info(f"System prompt length: {len(system_prompt_content)}")

    # Convert request messages to LangGraph format
    lc_messages = []
    for msg in request.messages:
        content = msg.content or ""
        if msg.role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif msg.role == "assistant":
            lc_messages.append(AIMessage(content=content))
        elif msg.role == "system":
            lc_messages.append(SystemMessage(content=content))

    # Extract citizen profile from system prompt dynamic variables.
    # ElevenLabs injects {{first_name}} etc. into system prompt after
    # successful auth via workflow dispatch tool. This is checked on
    # EVERY turn so authenticated state persists across the conversation.
    import re

    # Extract workflow node from "Specific goal" in system prompt.
    # ElevenLabs injects each subagent's additional_prompt as "Specific goal".
    # We use [NODE:xxx] markers to route directly to the right LangGraph node.
    workflow_node = None
    if system_prompt_content:
        node_match = re.search(r'\[NODE:(\w+)\]', system_prompt_content)
        if node_match:
            workflow_node = node_match.group(1)
            logger.info(f"Workflow node detected: {workflow_node}")

        # Extract citizen profile from dynamic variables
        name_match = re.search(r'Vatandasin adi:\s*(\w+)', system_prompt_content)
        id_match = re.search(r'Vatandas ID:\s*(\d+)', system_prompt_content)
        ref_match = re.search(r'Basvuru numarasi:\s*([\w-]+)', system_prompt_content)
        status_match = re.search(r'Basvuru durumu:\s*(\w+)', system_prompt_content)

        if name_match:
            auth_status = "authenticated"
            _citizen_id = int(id_match.group(1)) if id_match else None
            citizen_profile = {
                "first_name": name_match.group(1),
                "citizen_id": _citizen_id,
                "application_ref": ref_match.group(1) if ref_match else "",
                "application_status": status_match.group(1) if status_match else "",
                "gender": "M",  # default, overridden below if DB available
            }
            # Look up gender from DB
            if _citizen_id:
                try:
                    from api.models import Citizen, SessionLocal
                    _gdb = SessionLocal()
                    _cit = _gdb.query(Citizen).filter(Citizen.id == _citizen_id).first()
                    if _cit and hasattr(_cit, "gender"):
                        citizen_profile["gender"] = _cit.gender
                    _gdb.close()
                except Exception:
                    pass
            logger.info(f"Authenticated via dynamic variables: {citizen_profile}")

    # Detect post-auth first turn: if many messages (>8) and last user
    # message is an auth artifact (single letter, short number), find the
    # user's ORIGINAL request from the beginning of conversation.
    if lc_messages and len(request.messages) > 8 and auth_status == "authenticated":
        user_msgs = [m for m in lc_messages if isinstance(m, HumanMessage)]
        if user_msgs:
            last_content = user_msgs[-1].content.strip()
            is_auth_artifact = len(last_content) <= 4

            if is_auth_artifact:
                def _is_auth_data(text: str) -> bool:
                    t = text.strip().lower()
                    if len(t) <= 4:
                        return True
                    if re.match(r'^\d{1,2}[\s/.\-]\w+[\s/.\-]\d{4}$', t):
                        return True
                    if t.replace(" ", "").replace("-", "").replace(".", "").isdigit():
                        return True
                    return False

                original_request = None
                for m in user_msgs:
                    content = m.content.strip()
                    if not _is_auth_data(content):
                        original_request = content
                        break

                if original_request:
                    logger.info(f"Post-auth first turn — using original request: '{original_request[:50]}'")
                    lc_messages = [HumanMessage(content=original_request)]

    # ElevenLabs sends full message history with every request (stateless).
    # Pass all messages to the graph — no checkpointer needed.
    graph = _compiled_graph

    async def stream():
        response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        has_tool_calls = False

        yield sse_chunk(response_id, {"role": "assistant"})

        # Buffer words — give TTS something to say while LangGraph processes.
        # Prevents dead air during the 2-5 second graph execution.
        if auth_status == "authenticated" and not _cb_is_open():
            import asyncio
            yield sse_chunk(response_id, {"content": "Let me check that for you. "})
            await asyncio.sleep(0.05)

        if _cb_is_open():
            # Level 1 degradation: bypass LangGraph, direct OpenAI call
            logger.warning(f"Circuit breaker open — using Level 1 fallback | conv={conversation_id}")
            async for chunk in _level1_fallback(lc_messages, language):
                yield sse_chunk(response_id, {"content": chunk})
        else:
            try:
                graph_input = {"messages": lc_messages, "language": language}

                if workflow_node:
                    graph_input["workflow_node"] = workflow_node

                if auth_status != "unauthenticated":
                    graph_input["auth_status"] = auth_status
                if citizen_profile:
                    graph_input["citizen_profile"] = citizen_profile

                # Step 1: LangGraph determines intent, runs tools, gets context
                # (no LLM streaming here — just routing + tool calls)
                import time as _time
                _start = _time.time()
                result = await graph.ainvoke(graph_input)
                _elapsed_ms = int((_time.time() - _start) * 1000)

                final_messages = result.get("messages", [])
                if not final_messages:
                    yield sse_chunk(response_id, {"content": "Bir hata olustu."})
                else:
                    last_msg = final_messages[-1]
                    tool_calls = getattr(last_msg, "additional_kwargs", {}).get("tool_calls")

                    if tool_calls:
                        has_tool_calls = True
                        tool_calls_delta = [
                            {
                                "index": i,
                                "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                "type": "function",
                                "function": {
                                    "name": tc["function"]["name"],
                                    "arguments": tc["function"]["arguments"],
                                },
                            }
                            for i, tc in enumerate(tool_calls)
                        ]
                        yield sse_chunk(response_id, {"tool_calls": tool_calls_delta})

                    # Step 2: Stream the response sentence by sentence with
                    # small delays between sentences. This gives ElevenLabs TTS
                    # time to process each sentence before the next arrives —
                    # mimicking real LLM token-by-token timing.
                    content = getattr(last_msg, "content", None)
                    if content and content.strip():
                        import asyncio
                        # Split by sentence boundaries
                        import re
                        sentences = re.split(r'(?<=[.!?])\s+', content.strip())

                        for i, sentence in enumerate(sentences):
                            if sentence.strip():
                                yield sse_chunk(response_id, {"content": sentence + " "})
                                # Small delay between sentences for TTS processing
                                if i < len(sentences) - 1:
                                    await asyncio.sleep(0.08)

                _cb_record_success()

                # Log analytics
                try:
                    from api.models import ConversationLog, SessionLocal
                    _db = SessionLocal()
                    _db.add(ConversationLog(
                        conversation_id=conversation_id,
                        intent=result.get("current_intent", ""),
                        workflow_node=workflow_node or "",
                        auth_status=auth_status,
                        citizen_id=citizen_profile.get("citizen_id") if citizen_profile else None,
                        language=language,
                        message_count=len(request.messages),
                        response_time_ms=_elapsed_ms,
                        rag_query=result.get("rag_query"),
                        rag_score=result.get("rag_score"),
                        intent_classify_ms=result.get("timing_intent_classify"),
                        service_node_name=result.get("service_node_name"),
                        tool_chain_triggered=result.get("tool_chain_triggered"),
                        api_calls_count=result.get("api_calls_count"),
                    ))
                    _db.commit()
                    _db.close()
                except Exception as _e:
                    logger.warning(f"Analytics log failed: {_e}")

            except Exception as e:
                logger.error(f"Graph execution error: {e}")
                _cb_record_failure()
                yield sse_chunk(response_id, {"content": "Bir hata olustu. Lutfen tekrar deneyin."})

        finish_reason = "tool_calls" if has_tool_calls else "stop"
        yield sse_chunk(response_id, {}, finish_reason=finish_reason)
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/health")
def health():
    cb = _cb_status()
    return {
        "status": cb["label"],
        "agent": "langgraph-citizen-services",
        "degradation_level": cb["level"],
        "consecutive_failures": cb["failures"],
    }
