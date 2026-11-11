"""
Escalation node.

Generates an empathetic transfer message based on conversation context.
Uses LLM to adapt tone — frustrated caller gets acknowledgment,
normal request gets standard transfer message.

Also generates a context summary for the human operator and logs
the handoff event via /handoff endpoint.

In production with Twilio (ENABLE_PHONE_TRANSFER=true), also returns
a transfer_to_number system tool call with operator context.
"""

import json
import os
import uuid

from langchain_core.messages import AIMessage, SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from agent.state import AgentState
from agent.logging_config import get_logger
from agent.nodes.utils import mark_completed, get_honorific, gov_api_request

logger = get_logger(__name__)

_TRANSFER_NUMBER = "+905001234567"
_ENABLE_TRANSFER = os.getenv("ENABLE_PHONE_TRANSFER", "false").lower() == "true"

_llm = ChatOpenAI(model="gpt-4o", temperature=0.3)

ESCALATE_PROMPT = """You are a government citizen services assistant named Umut.
The caller needs to be transferred to a human operator.

Based on the caller's last message, generate a SHORT (1-2 sentences) transfer message.
Your response will be read aloud by text-to-speech.

Rules:
- If the caller is frustrated or angry: acknowledge their frustration first, then transfer
  Example: "Yasadiginiz sorunu anliyorum ve ozur dilerim. Sizi hemen bir yetkiliyle gorusturecegim."
- If the caller simply requested an operator: standard polite transfer
  Example: "Sizi bir operatore bagliyorum, lutfen bir an bekleyin."
- Never use formatting, bullet points, or numbers
- Respond in {language_name}
- Keep it warm and human — this person is about to talk to a real person"""

SUMMARY_PROMPT = """Summarize this conversation for a human operator in 2-3 sentences.
Include: what the citizen wanted, what was done, why they're being transferred.
If the citizen is authenticated, mention their name.
Write in {language_name}. Be concise and factual."""


def _generate_operator_summary(messages: list, language: str) -> str:
    """Generate conversation summary for the human operator."""
    language_name = "English" if language == "en" else "Turkish"
    conv_messages = [m for m in messages if not isinstance(m, SystemMessage)]

    try:
        response = _llm.invoke([
            SystemMessage(content=SUMMARY_PROMPT.format(language_name=language_name)),
            *conv_messages[-10:],  # Last 10 messages for context
        ])
        return response.content
    except Exception as e:
        logger.warning(f"Could not generate operator summary: {e}")
        return "Vatandas operator yardimi talep ediyor." if language != "en" else "Citizen requesting operator assistance."


def _log_handoff(session_id: str, reason: str, summary: str, language: str):
    """Log handoff event via API."""
    try:
        r = gov_api_request("POST", "/handoff", json={
            "session_id": session_id or f"escalate-{uuid.uuid4().hex[:8]}",
            "reason": reason,
            "language": language,
        }, timeout=3.0)
        if r.status_code != 200:
            logger.warning(f"Handoff API returned {r.status_code} — handoff not audited")
    except Exception as e:
        logger.warning(f"Could not log handoff: {e}")


def _detect_escalation_reason(messages: list) -> str:
    """Detect why the caller is being escalated from conversation context."""
    if not messages:
        return "caller_request"

    last_user_msg = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            last_user_msg = msg.content.lower()
            break

    # Check for frustration indicators
    frustration_words = ["kizgin", "sinirli", "sacma", "rezalet", "berbat",
                         "angry", "frustrated", "ridiculous", "terrible"]
    if any(w in last_user_msg for w in frustration_words):
        return "frustration"

    return "caller_request"


def escalate(state: AgentState) -> dict:
    """Escalate to human operator with context summary and empathetic message."""
    language = state.get("language", "tr")
    messages = state.get("messages", [])
    profile = state.get("citizen_profile")
    language_name = "English" if language == "en" else "Turkish"

    # Get last user message for tone
    user_msg = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            user_msg = msg.content
            break

    # Generate empathetic transfer message for caller
    response = _llm.invoke([
        SystemMessage(content=ESCALATE_PROMPT.format(language_name=language_name)),
        HumanMessage(content=user_msg or "Operator ile gorusmek istiyorum"),
    ])
    client_message = response.content

    # Generate context summary for operator
    operator_summary = _generate_operator_summary(messages, language)

    # Detect escalation reason
    reason = _detect_escalation_reason(messages)

    # Add citizen info to summary if authenticated
    if profile:
        honorific = get_honorific(profile, language)
        citizen_id = profile.get("citizen_id", "")
        operator_summary = f"Citizen: {honorific} (ID: {citizen_id}). {operator_summary}" if language == "en" else f"Vatandas: {honorific} (ID: {citizen_id}). {operator_summary}"

    # Log handoff
    _log_handoff(
        session_id=state.get("messages", [{}])[0].content[:20] if messages else "",
        reason=reason,
        summary=operator_summary,
        language=language,
    )

    logger.info(f"Escalation | reason={reason} | transfer_enabled={_ENABLE_TRANSFER}")
    logger.info(f"Operator summary: {operator_summary}")

    if _ENABLE_TRANSFER:
        tool_call = {
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {
                "name": "transfer_to_number",
                "arguments": json.dumps({
                    "reason": reason,
                    "transfer_number": _TRANSFER_NUMBER,
                    "client_message": client_message,
                    "agent_message": operator_summary,
                }),
            },
        }
        msg = AIMessage(
            content=client_message,
            additional_kwargs={"tool_calls": [tool_call]},
        )
    else:
        msg = AIMessage(content=client_message)

    return {
        "messages": [msg],
        "completed_intents": mark_completed(state, "escalate"),
        "service_node_name": "escalate",
        "api_calls_count": 1,  # handoff log
    }
