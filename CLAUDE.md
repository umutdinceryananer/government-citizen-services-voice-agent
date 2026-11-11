# CLAUDE.md

## Project Overview
Government Citizen Services Voice Agent — an AI-powered multilingual voice agent (Turkish + English) for government citizen services. Built on ElevenLabs Conversational AI with a custom LangGraph backend, RAG pipeline (Pinecone), and Streamlit analytics dashboard.

**Context:** This is an FDE (Forward Deployed Engineer) demo project for ElevenLabs. The architecture should demonstrate both platform mastery (ElevenLabs native features) and engineering depth (Custom LLM + LangGraph). Reference: Oscar's guidance says LangGraph + Custom LLM is the expected approach.

## Tech Stack
- **Voice Layer:** ElevenLabs Conversational AI (STT + TTS)
- **Agent Backend:** LangGraph (multi-step workflow orchestration)
- **API Bridge:** FastAPI (Custom LLM endpoint for ElevenLabs)
- **RAG:** Pinecone (vector DB) + OpenAI embeddings
- **Database:** SQLite (demo) / PostgreSQL (production) via SQLAlchemy ORM
- **Dashboard:** Streamlit
- **Language:** Python 3.11+

## Architecture
```
ElevenLabs (platform-native):          LangGraph (custom intelligence):
├─ Voice (STT/TTS)                     ├─ Intent classification (GPT-4o)
├─ Language detection (system tool)    ├─ Multi-step service routing
├─ Auth gating (workflow dispatch)     ├─ Tool orchestration (API calls)
└─ Degradation fallback                ├─ Deterministic tool chaining
                                       ├─ Stateless (full history per request)
                                       └─ Prompt versioning

ElevenLabs Workflow (visual routing):
  Start → Collect Identity (GPT-4o-mini + native KB for FAQ)
    → Dispatch tool (webhook auth) → Success → Service Router (GPT-4o)
      → Status Check (Custom LLM / LangGraph) [NODE:status_check]
      → Appointment (Custom LLM / LangGraph) [NODE:appointment_book]
      → Document Request (Custom LLM / LangGraph) [NODE:document_request]
      → Complaint (Custom LLM / LangGraph) [NODE:complaint]
      → Operator Transfer (phone)
      → End
    → Failure → Auth Retry (GPT-4o-mini) → back to Collect Identity

Single FastAPI server (agent/server.py, port 8080):
├─ /v1/chat/completions — Custom LLM proxy (ElevenLabs → LangGraph → SSE)
├─ /auth/*              — Government auth endpoints (mounted from api/)
├─ /applications*       — Application status (X-Internal-Key)
├─ /appointments/*      — Appointment endpoints (X-Internal-Key; /appointments/slots/* public)
├─ /documents/*         — Document request endpoints (X-Internal-Key)
├─ /handoff             — Handoff audit log (X-Internal-Key); /guest/info public
├─ /citizens/{id}       — GDPR erasure (X-Admin-Key only — never the voice flow)
├─ /services            — Service catalog (public)
└─ /health              — Health check + circuit breaker status
```

## Project Structure
```
/agent              — LangGraph agent core
  /nodes            — Graph node implementations (intent_classify, status_check, appointment_list, appointment_cancel, document_status, etc.)
  /prompts          — Versioned system prompts (v1.0/system_prompt_tr.md, _en.md)
  /tools            — Validators + schemas (tc_kimlik.py, app_ref.py, date_parser.py, schemas.py — system tools only)
  config.py         — AgentConfig dataclass (incl. custom_llm_url)
  deploy.py         — Programmatic agent deploy to ElevenLabs (conversation config, workflow preserved)
  graph.py          — LangGraph StateGraph definition
  logging_config.py — PII redaction logging
  server.py         — Custom LLM proxy + mounted gov API (/v1/chat/completions SSE, circuit breaker)
  state.py          — AgentState TypedDict (incl. node detail tracking fields)
/api                — Government backend (mounted into agent/server.py)
  auth.py           — POST /auth/verify/tc-kimlik, /auth/verify/app-ref, /auth/verify/webhook
  handoff.py        — router (internal key): POST /handoff; guest_router (public): POST /guest/info
  models.py         — SQLAlchemy models (Citizen, Application, AuthAuditLog, Appointment, DocumentRequest, ConversationLog)
  seed_data.py      — 23 citizen records + 26 applications + 5 appointments + 3 doc requests seeder
  security.py       — require_internal_key (X-Internal-Key) / require_admin_key (X-Admin-Key) dependencies, hmac.compare_digest, fail closed
  services.py       — router (internal key): GET /applications/{ref}, GET /applications, POST /appointments, GET /appointments/{citizen_id}, DELETE /appointments/{id}, POST /documents/request, GET /documents/{citizen_id}; public_router: GET /services, GET /appointments/slots/{service_type}; admin_router: DELETE /citizens/{id}
  server.py         — Standalone FastAPI app (for independent testing)
/rag                — RAG pipeline
  chunker.py        — Document chunking (header-based + size overlap)
  embed.py          — Embedding pipeline (OpenAI → Pinecone upsert)
  retriever.py      — Pinecone query with language/category filters
/dashboard          — Streamlit analytics dashboard (9-page sidebar navigation)
  app.py            — Multi-page dashboard (Overview, Intents, Auth, Knowledge Gaps, Flows, Node Details, Health, Insights, Logs)
  insights.py       — LLM-powered recommendation engine (GPT-4o + ElevenLabs docs RAG)
/data               — citizens.db (SQLite, gitignored), knowledge_base/ (40 docs + manifest.json)
/docs               — auth_flow.md, conversation_flow.md
/tests              — Unit, integration, edge case, and live API tests (293 tests)
  test_core_system.py — Comprehensive core system tests (104 tests, all product endings)
  test_api_security.py — API key guards: 403 without/wrong key, admin-only erasure, public routes, nodes pass key (60 tests)
  conftest.py       — Shared test DB setup with per-test audit log cleanup
  voice_e2e_test_roadmap.md — 31 manual voice test scenarios
  /eval             — Automated conversation evaluation (planned)
```

## Key Design Decisions
- **ElevenLabs Workflows for auth gating** — deterministic dispatch tool + subagent isolation (not LLM-based)
- **LangGraph for intelligence** — intent routing, tool chaining, state management (what ElevenLabs native can't guarantee)
- **LangGraph's core differentiator:** deterministic tool chaining (status "additional_docs_needed" → auto RAG query, "rejected" → appeal guidance)
- **Single multilingual agent** with language_presets — one endpoint, auto language detection
- **KVKK compliance** — PII redaction in all logs, TC Kimlik stored as SHA-256 hash, audit trail with no raw PII
- **Prompt versioning** tracked per conversation for data-driven optimization
- **Workflow visual routing + LangGraph node intelligence** — workflow handles high-level routing between conversation phases, LangGraph handles tool chaining, RAG, API orchestration inside each node
- **GPT-4o for Service Router** — enables reliable workflow edge transitions. Custom LLM on Service Router causes workflow edges not to trigger
- **[NODE:xxx] markers in workflow additional prompts** — LangGraph reads these to identify which workflow node is active
- **All LangGraph nodes use GPT-4o** — upgraded from GPT-4o-mini for better context understanding
- **citizen_id resolved via {{citizen_id}} dynamic variable** from dispatch tool assignments
- **Pre-auth FAQ via ElevenLabs native knowledge base** — platform mastery, no Custom LLM needed
- **Post-auth operations via Custom LLM / LangGraph** — deterministic tool chaining, RAG, API orchestration
- **Stateless message passing** — ElevenLabs sends full history each turn, dynamic variables parsed every turn, no server-side checkpointer needed
- **Dynamic context management** — intent_classify receives full filtered conversation history (system messages excluded), enabling follow-up understanding and entity resolution without extra LLM calls. Approach: LangChain "select" strategy over "compress" to avoid voice latency
- **Graceful degradation** — Level 0: full LangGraph, Level 1: direct OpenAI (circuit breaker after 3 failures, 60s cooldown), Level 2: ElevenLabs native fallback (backup_llm_config)
- **Sentence-level SSE streaming** — responses split by sentence with delays for TTS processing
- **STT-friendly auth** — last 4 digits + DOB + father initial instead of full 11-digit TC Kimlik
- **Shared-secret guard on the public gov API** — gov routers share the ngrok-exposed app, so citizen-data routes require X-Internal-Key (only LangGraph nodes hold it) and GDPR erasure requires a separate X-Admin-Key. Fail closed: unset key → every request 403. Chosen over moving the API off the public app to keep the single-server/single-tunnel setup

## README Policy
- README contains the FULL target project structure — not just what exists today
- Never strip future/planned items from README — an evaluator should see the full vision upfront

## Development Guidelines
- Always use `from agent.logging_config import get_logger` — never use `print()` or raw `logging`
- PII redaction is automatic — TC Kimlik (11 digits) and DOB patterns are masked before log output
- Use `.env` for secrets, never commit API keys
- System prompts live in versioned files under `/agent/prompts/`
- Shared node utilities in `agent/nodes/utils.py` (mark_completed, gov_api_request)
- Nodes call the government API only through `gov_api_request()` — it attaches X-Internal-Key; never call `httpx.get/post` against the API directly
- New citizen-data routes go on the guarded `router` in api/services.py or api/handoff.py; only static/read-only info goes on `public_router` / `guest_router`
- `load_dotenv()` is called once in `agent/graph.py` — not in individual nodes
- Tests: `python -m pytest tests/ -v` (293 tests collected)
- Test DB: in-memory SQLite via conftest.py, audit log cleaned per test
- Test API keys: conftest.py sets INTERNAL_API_KEY / ADMIN_API_KEY to fixed test values (TEST_INTERNAL_KEY / TEST_ADMIN_KEY) — API test clients must send them
- Service nodes report `service_node_name`, `tool_chain_triggered`, `api_calls_count` to state for analytics

## User Preferences (for Claude)
- User communicates in Turkish
- User does manual git commits — provide commit messages with Co-Authored-By line, never auto-commit
- Update CLAUDE.md after every development step
- Update issues.md task checkboxes as work completes
- Think like a senior FDE — business value over over-engineering
- Ask questions before making assumptions
- When editing README, keep full target structure (don't strip planned items)

## How to Run
```bash
# 1. Clone and setup
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Fill in: ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, OPENAI_API_KEY,
#          INTERNAL_API_KEY, ADMIN_API_KEY (required — gov API refuses all citizen-data requests without INTERNAL_API_KEY)
# Generate keys: python -c "import secrets; print(secrets.token_urlsafe(32))"

# 3. Seed the citizen database
python -m api.seed_data

# 4. Start server (single server — gov API mounted into agent server)
python -m uvicorn agent.server:app --reload --port 8080

# 5. Start ngrok tunnel (for ElevenLabs to reach our server)
ngrok http 8080

# 6. Deploy agent to ElevenLabs (conversation config only — workflow preserved)
$env:CUSTOM_LLM_URL="<ngrok-url>"; python -m agent.deploy
python -m agent.deploy --dry-run # Preview without deploying

# 7. Run tests
make test              # Unit tests (no API keys needed for most)
make test-live         # Live API + simulation tests
```

## Current Status

### Completed Issues
- **ISSUE-01:** Project Setup — repo structure, .env, requirements, logging with PII redaction
- **ISSUE-02:** Agent Persona — system prompts v1.0 (TR+EN), 7 intents, guardrails, deploy script, 11 simulation tests
- **ISSUE-03:** Multilingual — single agent with language_presets, language detection system tool, 5 language tests
- **ISSUE-04:** Auth Flow Design — workflow-based deterministic auth (ElevenLabs blog pattern), KVKK compliance, TC Kimlik checksum
- **ISSUE-05:** Auth Implementation — SQLite+SQLAlchemy, FastAPI auth endpoints, 20 tests, progressive failure guidance
- **ISSUE-06:** Failure Handling — handoff endpoint, guest mode FAQ, 10 tests
- **ISSUE-07:** LangGraph Workflow — 8-node graph, LLM intent classification, deterministic tool chaining, Custom LLM proxy with SSE streaming, 24 tests
- **ISSUE-08:** Connect LangGraph to ElevenLabs Voice — transfer_to_number system tool call, buffer words, circuit breaker (Level 0/1/2 degradation), Custom LLM deploy config, backup_llm_config, conversation.py removed
- **ISSUE-09:** Tool Schemas — 3 system tools (language_detection + end_call deployed, transfer_to_number deferred to Twilio)
- **ISSUE-10:** Knowledge Base — 40 documents (5 categories × 4 doc types × 2 languages), manifest.json
- **ISSUE-11:** Embedding Pipeline — chunker (219 chunks from 40 docs, min 50 chars), OpenAI text-embedding-3-small, Pinecone serverless index, 5/5 validation queries passing
- **ISSUE-12:** RAG Integration — faq_answer grounded in Pinecone (no hallucination), status_check chains to RAG for required docs + appeal rights
- **ISSUE-13:** Mock Government API — Application, Appointment, DocumentRequest DB models, 7 API endpoints, seed data (23 citizens, 26 applications, 5 appointments, 3 doc requests)
  - **Debt:** Application table 1:N separation — DONE
- **ISSUE-14:** Tool Calling — Nodes connected to real API via httpx, tool chaining (status → RAG), 15 services tests
  - **Debt:** appointment_list, appointment_cancel, document_status nodes — DONE
- **ISSUE-15:** Edge Cases — LLM-based handling (no hardcoded keywords), platform settings, TTS-friendly SSE streaming
- **ISSUE-16:** Conversation Context Management — full filtered history, dynamic context, GPT-4o intent_classify
- **ISSUE-17:** Multi-Intent Flows — workflow backward edges + LangGraph intent_classify fallback

### Auth Workflow (ISSUE-05B) — COMPLETE
- ElevenLabs Workflow: Start → Collect Identity (GPT-4o-mini + native KB for FAQ) → Dispatch tool (webhook) → Success → Service Router (GPT-4o) → Status Check / Appointment / Document Request / Complaint / Operator Transfer / End. Failure → Auth Retry (GPT-4o-mini) → back to Collect Identity
- Auth method: TC Kimlik last 4 digits + DOB + father's name initial (STT-friendly)
- Webhook: POST /auth/verify/webhook — returns 200 on success, 401 on failure (generic error message, no field-specific info leak)
- Dynamic variables: first_name, application_ref, application_status injected into system prompt by ElevenLabs
- Dynamic variables extracted from system prompt every turn for persistent auth state
- Workflow configured via dashboard (tool node requires dashboard webhook config), deploy script preserves workflow

### What Works Now (e2e tested via dashboard):
- ✅ FAQ/RAG — "Çalışma saatleri?", "Pasaport belgeleri?", "Ehliyet ücreti?" (TTS-friendly, no digits)
- ✅ Auth flow — collect identity → dispatch tool → success/failure routing
- ✅ Auth + status check — "Başvurumun durumunu öğrenmek istiyorum" → auth → "Ahmet, başvurunuz inceleme aşamasında"
- ✅ Auth failure — wrong credentials → Auth Retry → "tekrar denemek ister misiniz?"
- ✅ Edge cases — auth refusal, third-party block, robot question, anger → escalate
- ✅ Complaint recording
- ✅ Operator transfer (demo mode)
- ✅ Multi-application status check — lists all citizen applications, user selects for detail
- ✅ Appointment booking with service type detection — detects service from conversation history, asks clarifying question if ambiguous
- ✅ Appointment cancellation
- ✅ Document request with type detection — detects document type from conversation history
- ✅ Clarifying questions for ambiguous requests
- ✅ Multi-intent in single conversation — status check → appointment → document → complaint transitions
- ✅ 293 tests (104 core system + auth + services + API security + edge cases + schemas + logging)
- ✅ 31 manual voice e2e test scenarios documented

### Twilio Phone Integration (ISSUE-15B) — IN PROGRESS
- Twilio trial account created, US number: +1 (740) 554-8808
- Number imported to ElevenLabs dashboard, agent assigned for inbound calls
- Voice webhook: https://api.us.elevenlabs.io/twilio/inbound_call
- Agent answers but call drops — likely ElevenLabs credit exhaustion
- Pending: Twilio personalization webhook for hybrid silent auth (caller_id → DB lookup)
- Pending: E2E voice test when credits renewed

### Analytics Dashboard — COMPLETE
- 9-page Streamlit dashboard with sidebar navigation
- Pages: Overview, Intents & Performance, Auth & Language, Knowledge Gaps, Conversation Flows (Sankey), Node Details, System Health, AI Insights, Logs
- Node Details page: per-node timing, tool chain analysis, API call volume
- Conversation Flows: Sankey diagram, common paths, entry/exit points, conversation depth
- AI Insights: GPT-4o + ElevenLabs docs RAG recommendations (basic, deferred for multi-agent redesign)
- ConversationLog tracks: intent, response_time_ms, intent_classify_ms, rag_query/score, service_node_name, tool_chain_triggered, api_calls_count

### Dead Code Cleanup (ISSUE-36) — COMPLETE
- Removed build_auth_workflow() from deploy.py (workflow managed via dashboard)
- Removed dead Pydantic input classes + TOOL_SCHEMAS from schemas.py
- Removed unused timer() from utils.py
- Removed debug file write (last_system_prompt.txt) from server.py

### Test Suite Consolidation (ISSUE-41) — COMPLETE
- Merged test_graph.py (36 duplicate tests) into test_core_system.py
- 231 total tests, 104 in comprehensive core system suite

### API Route Protection (ISSUE-44) — COMPLETE
- Problem: gov API routers share the ngrok-exposed app with /v1/chat/completions; no route checked the caller, so anyone with the URL could read/book/cancel/erase citizen data and write fake handoff audit rows
- api/security.py: `require_internal_key` (X-Internal-Key vs INTERNAL_API_KEY) and `require_admin_key` (X-Admin-Key vs ADMIN_API_KEY), hmac.compare_digest, 403 on missing/wrong key, fail closed when env var unset
- Router split: services.py → router (internal) / public_router (GET /services, GET /appointments/slots/*) / admin_router (DELETE /citizens/{id}); handoff.py → router (POST /handoff, internal) / guest_router (POST /guest/info, public)
- All 7 API-calling nodes go through `gov_api_request()` in agent/nodes/utils.py (per-node API_BASE constants removed); escalate now logs a warning when the handoff audit call is refused
- agent/server.py logs a startup warning when INTERNAL_API_KEY is unset
- tests/test_api_security.py — 60 tests (12 skip without OPENAI_API_KEY because importing agent.server / escalate builds ChatOpenAI)
- Not done (optional, from spec): moving the gov API off the public app entirely

### Technical Debt
- POST /twilio/inbound (api/twilio_webhook.py) is unguarded and returns first name + citizen_id for a known phone number — needs ElevenLabs/Twilio webhook auth (secret header or signature), out of ISSUE-44 scope
- Rate limit not enforced: slowapi Limiter(default_limits=["60/minute"]) is configured in agent/server.py, but there is no SlowAPIMiddleware and no @limiter.limit decorator, so no route is limited (verified: 80 rapid requests → 80× 200). /auth/verify/* also has no server-side lockout (attempt_number is client-supplied)
- Workflow backward edges don't trigger with Custom LLM — LangGraph handles topic changes via intent_classify as fallback
- Service Router must use GPT-4o (not Custom LLM) for workflow edge transitions
- Buffer words after auth ("kontrol ediyorum") — causes workflow edge issues, deferred
- 10 pre-existing test failures (Turkish simulation tests + appointment date hardcoding)

### Remaining Issues
- **ISSUE-15B:** Twilio Phone Integration — blocked (credits exhausted)
- **ISSUE-26-28:** Documentation & Demo — deferred (separate day)
- **ISSUE-31, 35, 37-39:** Deferred to P3 (require production data volume)
