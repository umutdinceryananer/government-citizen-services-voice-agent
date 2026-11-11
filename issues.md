# ISSUE-01: Project Setup & ElevenLabs Agent Initialization

## Module
Foundation

## Priority
P0 — Blocker for all other issues

## Dependencies
None

## Description
Set up the project repository, configure the ElevenLabs development environment, and create the initial Conversational AI agent. This is the base layer everything else builds on.

## Tasks
- [x] Create GitHub repo with proper structure: `/agent`, `/api`, `/dashboard`, `/docs`, `/tests`
- [x] Set up Python virtual environment and `requirements.txt` with initial dependencies (elevenlabs SDK, langchain, pinecone-client, streamlit, fastapi)
- [x] Create `.env.example` with required environment variables (ELEVENLABS_API_KEY, PINECONE_API_KEY, OPENAI_API_KEY)
- [x] Create initial agent on ElevenLabs Conversational AI platform via dashboard
- [ ] Verify agent responds to a basic test call (voice in, voice out)
- [x] Set up `.gitignore` (env files, __pycache__, .venv)
- [x] Write initial README with project overview and setup instructions
- [x] Structured logging with automatic PII redaction (KVKK compliance)

## Acceptance Criteria
- [x] Repo is live on GitHub with clean folder structure
- [x] Agent is created on ElevenLabs platform and accessible via API key
- [ ] A test call works end-to-end: user speaks, agent responds with a default greeting
- [x] Another developer can clone the repo, follow README, and run the project locally

## Notes
- Use ElevenLabs Conversational AI platform, not the basic TTS API
- Keep the agent simple at this stage — no custom LLM, no tools, just a working voice loop
# ISSUE-02: Agent Persona & Base Conversation Flow

## Module
Foundation

## Priority
P0

## Dependencies
ISSUE-01

## Description
Define the agent's identity, system prompt, and base conversation flow for a government citizen services use case. The agent should behave like a professional, patient, multilingual government support representative.

## Tasks
- [x] Write system prompt that defines the agent persona: name, role, tone, boundaries
- [x] Define the core conversation flow:
  1. Greeting — agent introduces itself and asks how it can help
  2. Intent detection — agent identifies what the citizen needs (status check, appointment, document request, general question, fee inquiry, complaint, human transfer)
  3. Routing — based on intent, agent follows the appropriate sub-flow
  4. Closing — agent confirms resolution and asks if anything else is needed
- [x] Define out-of-scope handling — what happens when someone asks something the agent cannot help with
- [x] Define conversation guardrails — agent should not provide legal advice, should not share other citizens' data, should not make promises about application outcomes
- [x] Implement the base flow on ElevenLabs platform (programmatic deploy via API)
- [x] Test with at least 5 different opening prompts to verify intent detection works (11 scenarios via simulation API)

## Acceptance Criteria
- [x] Agent greets the caller professionally in the detected language
- [x] Agent correctly identifies at least 3 intents: status check, appointment booking, general question (7 intents verified)
- [x] Agent gracefully handles out-of-scope requests without breaking the conversation
- [x] Guardrails prevent the agent from sharing sensitive information or making commitments

## Notes
- Keep the persona friendly but professional — this is a government service, not a casual chatbot
- Reference ElevenLabs blog on orchestration engine for context management patterns
# ISSUE-03: Multilingual Voice Support (Turkish + English)

## Module
Foundation

## Priority
P0

## Dependencies
ISSUE-02

## Description
Configure the agent to support Turkish and English voice interactions with automatic language detection. The agent should respond in the language the caller uses.

## Tasks
- [x] Configure Turkish voice model on ElevenLabs (primary language, flash v2.5 multilingual)
- [x] Configure English voice model on ElevenLabs (language preset, auto TTS model selection)
- [x] Implement language detection logic — platform-native language_detection system tool
- [x] Set up dynamic voice switching based on detected language (language_presets on single agent)
- [x] Translate system prompt and all static agent responses into both languages (v1.0 TR + EN prompts)
- [x] Test language detection accuracy with simulation tests (5 scenarios: TR, EN, EN status, TR→EN, EN→TR)
- [x] Handle mixed-language edge case — language detection system tool handles automatic switching via audio detection and explicit user requests

## Acceptance Criteria
- [x] Agent detects Turkish and responds in Turkish with Turkish voice
- [x] Agent detects English and responds in English with English voice
- [x] Language detection tool correctly triggers on language switch
- [ ] Voice quality is natural and clear in both languages (requires manual voice call verification)

## Notes
- Single agent with language_presets — one agent, one endpoint, auto-switches based on caller language
- Language detection is a system tool (not custom logic) — platform-native solution
- Consider adding Arabic as a third language later (ISSUE for future iteration, relevant for refugee population use case)
- ElevenLabs supports 70+ languages, so this is platform-native
- Oscar's Deutsche Telekom case study mentions real-time translation — reference that for architecture decisions
# ISSUE-04: Caller Identity Verification Flow Design

## Module
Authentication Flow

## Priority
P0

## Dependencies
ISSUE-02

## Description
Design the caller authentication flow where citizens verify their identity before accessing personal information like application status. This is critical for security and directly maps to ElevenLabs' blog post on secure caller identity authentication.

## Tasks
- [x] Define authentication methods:
  - Primary: TC Kimlik No (11-digit Turkish national ID) + Date of Birth
  - Secondary: Application Reference Number + Last Name
- [x] Design the conversation flow for authentication:
  1. Subagent 1 (unauthenticated): Greet, detect language, KVKK consent, collect credentials one by one
  2. Dispatch tool (verify_identity): Validate format (TC Kimlik checksum) + DB lookup
  3. Success edge → Subagent 2 (authenticated): Full tools access
  4. Failure edge → Retry (max 3) with progressive guidance, then human transfer
- [x] Document the flow as a state diagram (docs/auth_flow.md)
- [x] Define what data the agent is allowed to read back to the caller (e.g., first name yes, full TC kimlik no)
- [x] Design KVKK (Turkish GDPR) compliance requirements into the authentication flow:
  - Caller hears consent notice before personal data collection
  - Data retention: 30 days for PII, 90 days for auth results and transcripts
  - Data minimization: only collect what is strictly necessary
  - Log redaction: TC Kimlik masked (123****901), DOB masked (**/**/1990), audit trail has no raw PII
- [x] Implement TC Kimlik checksum validator (agent/tools/tc_kimlik.py) with masking utility

## Acceptance Criteria
- [x] Authentication flow is documented as a state diagram
- [x] Both verification methods are defined with clear field requirements
- [x] Retry and failure logic is specified (progressive guidance: simple retry → offer alt method → human transfer)
- [x] Security boundaries are defined (what agent can/cannot say)
- [x] KVKK compliance requirements are documented — consent flow, data retention, data minimization, log redaction rules

## Notes
- Architecture follows ElevenLabs Workflows: dispatch tool + subagent isolation (deterministic, not LLM-based gating)
- Reference: https://elevenlabs.io/blog/designing-secure-caller-identity-authentication-flows-for-voice-agents
- This is design only — implementation is ISSUE-05
- TC Kimlik validation has a checksum algorithm — implement it for format validation
# ISSUE-05: Implement Authentication Logic

## Module
Authentication Flow

## Priority
P0

## Dependencies
ISSUE-04 (design, done)
ISSUE-07 partially (LangGraph tool integration deferred — endpoint built independently, connected when Custom LLM bridge is ready)

## Description
Implement the caller identity verification logic designed in ISSUE-04. The agent collects credentials via voice, validates format, and checks against a mock citizen database.

**Scope decision:** Auth endpoint (FastAPI + SQLite) is built and tested independently. LangGraph/workflow tool integration happens in ISSUE-07/08 when Custom LLM bridge is ready. This follows real-world FDE practice: build backend first, integrate later.

## Tasks
- [x] Create mock citizen database (SQLite + SQLAlchemy ORM) with 23 sample records:
  - Fields: tc_kimlik (hashed), first_name, last_name, date_of_birth, application_ref, application_status, language_preference
  - SQLAlchemy abstraction allows production switch to PostgreSQL via connection string change
- [x] Implement TC Kimlik format validation (11 digits + checksum algorithm) — done in ISSUE-04
- [x] Implement application reference number format validation (YYYY-XX-NNNN)
- [x] Build FastAPI endpoints: `POST /auth/verify/tc-kimlik` and `POST /auth/verify/app-ref`
- [x] Implement KVKK compliance in authentication:
  - TC Kimlik stored as SHA-256 hash in DB, masked in logs (via PII redaction filter)
  - Audit trail (AuthAuditLog table) — no raw PII, only hashed citizen IDs
  - Data retention TTL — schema ready, purge logic deferred to ISSUE-07/08
- [x] 20 endpoint tests (TC Kimlik auth, app ref auth, progressive guidance, PII safety, audit trail)
- *Deferred to ISSUE-07/08:*
  - [ ] Integrate authentication as a tool in the LangGraph agent workflow
  - [ ] Implement retry logic via ElevenLabs workflow edges (dynamic variable: auth_attempt_count)
  - [ ] Implement session state via LangGraph state management

## Acceptance Criteria
- [x] `POST /auth/verify/tc-kimlik` and `POST /auth/verify/app-ref` return citizen profile on valid credentials
- [x] Invalid credentials return structured error with guidance message
- [x] TC Kimlik checksum rejects invalid numbers before DB lookup
- [x] TC Kimlik and DOB are masked/hashed in all stored logs
- [x] Audit trail captures all auth attempt events (no raw PII)
- [x] SQLAlchemy models are production-ready (migration-friendly schema)
- *Deferred to ISSUE-07/08:*
  - [ ] Agent collects TC Kimlik via voice and validates format
  - [ ] After 3 failed attempts, agent offers human transfer
  - [ ] Consent notice is delivered before any personal data collection

## Notes
- Voice number recognition edge cases ("bir iki üç" vs "123") will be handled in ISSUE-07/08 when voice integration is connected
- DB: SQLite for demo (zero setup), SQLAlchemy ORM for production-readiness (PostgreSQL swap = 1 line change)
- KVKK reference: https://www.kvkk.gov.tr — Turkey's Personal Data Protection Law (Law No. 6698)

---

# ISSUE-05B: Auth Workflow Integration (ElevenLabs Workflows + Custom LLM)

## Module
Authentication Flow

## Priority
P0

## Dependencies
ISSUE-04, ISSUE-05, ISSUE-08

## Description
Integrate ElevenLabs Workflows with Custom LLM for deterministic auth gating. Collect Identity (native GPT-4o) collects credentials, dispatch tool calls auth webhook, Authenticated Service (Custom LLM / LangGraph) handles post-auth services.

## Tasks
- [x] Add father_name to Citizen model and seed data
- [x] Create STT-friendly auth webhook (POST /auth/verify/webhook) — last 4 digits + DOB + father initial
- [x] Deploy ElevenLabs Workflow via API (start → collect → dispatch → success/failure)
- [x] Configure dispatch tool webhook in dashboard (URL, body params, assignments)
- [x] Set base agent LLM to Custom LLM, override Collect Identity + Auth Retry to GPT-4o
- [x] Parse dynamic variables (first_name, application_ref, application_status) from system prompt in Custom LLM proxy
- [x] Post-auth detection — identify auth artifacts, extract original user request
- [x] Auth failure returns HTTP 401 (not 200 with is_error) for correct workflow routing
- [x] Generic error messages — no field-specific information leakage
- [x] Mount government API routers into agent server (single port, single ngrok tunnel)

## Acceptance Criteria
- [x] Auth flow e2e: collect 3 fields → webhook → success → LangGraph responds with status
- [x] Auth failure e2e: wrong credentials → 401 → Auth Retry → offer to try again
- [x] Dynamic variables parsed from system prompt → citizen_profile available in LangGraph
- [x] Single server port (8080) — Custom LLM + gov API on same ngrok tunnel

---

# ISSUE-06: Authentication Failure Handling & Human Handoff

## Module
Authentication Flow

## Priority
P1

## Dependencies
ISSUE-05

## Description
Implement graceful failure handling when authentication fails or the caller cannot be verified. Include human agent transfer capability.

## Tasks
- [x] Progressive guidance per attempt — already implemented in ISSUE-05 auth endpoint (`_failure_guidance`)
- [ ] Build human handoff endpoint — `POST /handoff` simulates transfer, logs event with reason
- [ ] Implement "guest mode" — limited FAQ/general info access without authentication
- [x] Log all failed authentication attempts with reason codes — already implemented in ISSUE-05 (AuthAuditLog)
- *Deferred to ISSUE-07/08 (requires voice + LangGraph):*
  - [ ] 3-strike retry via ElevenLabs workflow edges (auth_attempt_count dynamic variable)
  - [ ] Caller frustration detection — LLM inference to skip retries and fast-track to human transfer
  - [ ] Voice-based handoff trigger with goodbye message

## Acceptance Criteria
- [x] `POST /handoff` logs transfer event and returns handoff confirmation
- [x] Guest mode allows general questions without authentication (bilingual FAQ, auth-required flagging)
- [x] All failure events are logged with timestamps and reason codes (done in ISSUE-05)
- *Deferred to ISSUE-07/08:*
  - [ ] After 3 failures, agent smoothly transitions to human handoff via workflow
  - [ ] Frustrated callers are fast-tracked to human transfer

---

# ISSUE-07: LangGraph Multi-Step Agent Workflow

## Module
Custom LLM Integration

## Priority
P0

## Dependencies
ISSUE-02

## Description
Build the core LangGraph agent that serves as the Custom LLM backend for ElevenLabs. This is the "brain" behind the voice agent — it receives conversation messages from ElevenLabs, performs multi-step reasoning (intent classification, tool orchestration, RAG), and streams responses back.

**Architecture:** ElevenLabs handles voice (STT/TTS), language detection (system tool), and auth gating (workflow). LangGraph handles everything the built-in LLM can't: deterministic tool chaining, typed state, complex business logic, and testable node isolation.

**Reference:** [Practical Guide: Open Source Agent Frameworks + ElevenAgents](https://elevenlabs.io/blog/practical-guide-open-source-agent-frameworks-and-elevenagents)

## Tasks
- [x] Design the LangGraph state schema (TypedDict):
  - `messages`: conversation history (Annotated with add_messages reducer)
  - `auth_status`: unauthenticated / authenticated
  - `citizen_profile`: dict (populated after auth)
  - `current_intent`: 7 intent types + unknown
  - `completed_intents`: list (track multi-intent handling)
  - `prompt_version`: str (which prompt version served this conversation)
- [x] Build the graph nodes (8 nodes):
  - `intent_classify` → LLM-based classification via GPT-4o-mini
  - `service_router` → passthrough, routing via conditional edges
  - `status_check` → deterministic tool chaining (additional_docs → auto docs, rejected → appeal)
  - `appointment_book` → books with mock slots
  - `document_request` → initiates document preparation
  - `faq_answer` → LLM + system prompt (RAG via Pinecone in ISSUE-10)
  - `complaint` → records complaint
  - `escalate` → human transfer message + transfer_to_number system tool call (ISSUE-08 ✓)
- [x] Implement deterministic tool chaining:
  - Scenario 1 — additional_docs_needed → auto required docs lookup → combined response ✓
  - Scenario 2 — rejected → appeal guidance with 30-day window ✓
  - Scenario 3 — escalate to human → transfer_to_number system tool call (ISSUE-08 ✓)
- [x] Implement multi-intent tracking via `completed_intents` state
- [x] Define conditional edges: START → intent_classify → service_router → [service nodes] → END
- [x] Implement prompt versioning in state
- [x] Write unit tests — 24 tests (node isolation + graph routing + full flow + SSE proxy)
- [x] Build Custom LLM proxy: FastAPI `/v1/chat/completions` SSE endpoint with MemorySaver checkpointer

## Acceptance Criteria
- [x] Agent correctly classifies intents and routes to appropriate service nodes (5 intent tests passing)
- [x] State persists via MemorySaver checkpointer keyed by conversation_id
- [x] Each node is independently testable with pytest (24 tests)
- [x] **Tool chaining test:** additional_docs_needed → response includes both status AND required documents ✓
- [x] **Business logic test:** rejected → appeal guidance ✓, approved → pickup info ✓, in_review → ETA ✓
- [x] Prompt versions tracked per conversation
- [ ] **Multi-intent test:** deferred — requires multi-turn checkpointer testing in ISSUE-08

---

# ISSUE-08: Connect LangGraph Agent to ElevenLabs Voice via Custom LLM

## Module
Custom LLM Integration

## Priority
P0

## Dependencies
ISSUE-07

## Description
Connect the LangGraph agent to ElevenLabs Conversational AI as a Custom LLM endpoint. The proxy server translates between ElevenLabs' OpenAI-compatible request format and LangGraph's streaming output, following the pattern from the FDE team's blog post.

**Three-step pattern (from FDE blog):**
1. Receive OpenAI-format chat completion request from ElevenLabs
2. Run LangGraph agent (filter tool calls, forward only model text)
3. Stream SSE chunks back in OpenAI-compatible format

## Tasks
- [x] Build Custom LLM proxy: FastAPI `/v1/chat/completions` endpoint (done in ISSUE-07)
  - Accept OpenAI-format messages + tools from ElevenLabs
  - Run LangGraph agent with `stream_mode="messages"`
  - Filter: only forward `langgraph_node == "model"` events (skip tool calls)
  - Stream SSE chunks: `data: {json}\n\n` + `data: [DONE]\n\n`
- [x] Implement `sse_chunk()` helper for OpenAI-compatible SSE formatting (done in ISSUE-07)
- [x] Handle system tools — escalate node returns `transfer_to_number` function call in OpenAI format, proxy forwards tool_calls in SSE delta with `finish_reason: "tool_calls"`
- [x] Implement buffer words for slow processing ("Bir saniye bakiyorum... ") — sent as first SSE chunk before LangGraph starts, trailing space per ElevenLabs docs
- [x] Remove `agent/conversation.py` (replaced by Custom LLM proxy)
- [x] Configure ElevenLabs agent to use Custom LLM endpoint (via deploy script) — `custom_llm.url` from `CUSTOM_LLM_URL` env var, auto-appends `/v1/chat/completions`
- [ ] Set up public URL (ngrok) for ElevenLabs to reach our server
- [ ] Test end-to-end: voice in → ElevenLabs STT → Custom LLM (LangGraph) → ElevenLabs TTS → voice out
- [ ] Measure and optimize latency — target under 500ms first-token response
- [x] Implement layered graceful degradation strategy:
  - **Level 0 (healthy):** Full LangGraph agent with all tools and RAG
  - **Level 1 (LangGraph degraded):** Circuit breaker (3 failures → direct OpenAI call, 60s cooldown)
  - **Level 2 (Custom LLM down):** ElevenLabs native fallback via `backup_llm_config: {preference: "default"}`
  - **Level 3 (full outage):** Deferred — requires phone/SIP infrastructure
  - [x] Circuit breaker pattern for auto-detection
  - [x] `/health` endpoint reports degradation level and consecutive failures

## Acceptance Criteria
- [ ] Voice call triggers LangGraph agent and receives streamed voice response
- [x] SSE output is OpenAI-compatible — ElevenLabs processes it correctly
- [x] Tool call events are filtered — only assistant text reaches TTS
- [x] System tool calls (transfer_to_number) are returned correctly
- [ ] Latency is under 500ms for first token
- [x] Buffer words maintain natural conversation flow during processing
- [x] Graceful degradation activates correctly per level (Level 0/1/2)

## References
- https://elevenlabs.io/blog/practical-guide-open-source-agent-frameworks-and-elevenagents
- https://elevenlabs.io/docs/eleven-agents/customization/llm/custom-llm

---

# ISSUE-09: Define Agent Tools & Function Schemas

## Module
Custom LLM Integration

## Priority
P1

## Dependencies
ISSUE-07

## Description
Define all tools the LangGraph agent can call, including their function schemas, input/output contracts, and error handling patterns.

## Tasks
- [x] Define tool schemas in OpenAI function calling format:
  - `check_application_status(application_ref)` → returns status details
  - `book_appointment(service_type, preferred_date)` → returns confirmation
  - `request_document(document_type)` → returns request confirmation
  - `file_complaint(description, category)` → returns confirmation
  - `search_knowledge_base(query, language)` → returns relevant FAQ/regulation excerpts
  - Note: `verify_identity` stays on ElevenLabs Workflow (dispatch tool), not LangGraph
  - Note: `transfer_to_human` implemented as system tool (transfer_to_number), deferred to ISSUE-15B/Twilio
- [x] Document each tool with descriptions, parameters, types via Pydantic models + OpenAI format
- [x] Implement tool validation — Pydantic input models with field validators (format, enum, length)
- [x] Build tool registry (`get_all_tool_schemas()`, `get_system_tool_configs()`)
- [x] Register system tools on ElevenLabs via deploy script (language_detection, end_call)
- [x] 22 schema/validation tests

## Acceptance Criteria
- [x] 5 business tools + 3 system tools defined with complete schemas
- [x] Tool selection handled by LangGraph intent_classify → service_router (not ElevenLabs)
- [x] Malformed inputs caught by Pydantic validators before API call
- [x] System tools centralized in schemas.py, deployed from single registry

---

# ISSUE-10: Knowledge Base Preparation

## Module
RAG Pipeline

## Priority
P1

## Dependencies
None (can start in parallel)

## Description
Prepare the knowledge base documents that the RAG pipeline will use to answer citizen questions about government services.

## Tasks
- [x] Create 40 government service documents across 5 categories:
  - Passport (gerekli belgeler, ücretler, başvuru süreci, SSS)
  - Civil Registry / Nüfus (doğum belgesi, ikametgah, evlilik)
  - Driver's License / Ehliyet (yeni, yenileme, kayıp)
  - Appointments / Randevu (nasıl alınır, iptal, ofis bilgileri, çalışma saatleri)
  - General / Genel (şikayet, itiraz, KVKK, iletişim)
- [x] Write documents in both Turkish and English (20 TR + 20 EN = 40 total)
- [x] Structure documents with metadata: category, language, doc_type, title
- [x] Store in `/data/knowledge_base/{category}/{language}/` directory structure
- [x] Create manifest.json listing all 40 documents with metadata

## Acceptance Criteria
- [x] 40 documents covering 5 government service categories (exceeds 30 minimum)
- [x] Each document exists in Turkish and English
- [x] Metadata complete in manifest.json (id, path, category, language, doc_type, title)
- [x] Documents are detailed — fees, step-by-step procedures, FAQs with realistic content

---

# ISSUE-11: Embedding Pipeline & Vector Store Setup

## Module
RAG Pipeline

## Priority
P1

## Dependencies
ISSUE-10

## Description
Build the embedding pipeline that processes knowledge base documents and loads them into Pinecone for semantic search.

## Tasks
- [x] Implement document chunking strategy — header-based splitting (##) with size overlap (800 chars, 100 overlap)
- [x] Select embedding model: OpenAI text-embedding-3-small (1536 dimensions, multilingual)
- [x] Build embedding pipeline: `python -m rag.embed` — chunk → embed → upsert to Pinecone
- [x] Configure Pinecone serverless index (AWS us-east-1, cosine metric, 1536 dims)
- [x] Implement metadata filters — language, category, doc_type stored per chunk
- [x] Build re-indexing: `python -m rag.embed --reset` deletes and recreates index
- [x] Build validation: `python -m rag.embed --validate-only` — 5 test queries (TR + EN)

## Acceptance Criteria
- [x] 253 chunks from 40 documents embedded and stored in Pinecone
- [x] 5/5 validation queries return correct category (passport, drivers_license, appointments, general)
- [x] Metadata filtering works — language filter tested in validation queries
- [x] Re-indexing via --reset flag recreates index without duplicates

---

# ISSUE-12: Integrate RAG into LangGraph Agent

## Module
RAG Pipeline

## Priority
P1

## Dependencies
ISSUE-07, ISSUE-11

## Description
Connect the RAG retrieval system to the LangGraph agent so it can answer citizen questions from the knowledge base.

## Tasks
- [x] Implement retriever module (`rag/retriever.py`) — Pinecone query with language + category filters
- [x] Add language-aware retrieval — filter by language metadata in Pinecone query
- [x] Implement context window management — top-3 chunks formatted with source attribution
- [x] Add source attribution — `[Source N: Title (category)]` prefix in context
- [x] Handle "no relevant results" — relevance threshold (0.3), graceful "I don't know" with operator offer
- [x] Implement answer grounding — RAG system prompt instructs LLM to only use provided context
- [x] Integrate RAG into faq_answer node — replaces direct LLM call
- [x] Integrate RAG into status_check node — tool chaining:
  - additional_docs_needed → auto RAG query for required documents (replaces hardcoded REQUIRED_DOCS)
  - rejected → auto RAG query for appeal rights from general category

## Acceptance Criteria
- [x] faq_answer uses Pinecone retrieval, not raw LLM knowledge
- [x] Answers grounded — system prompt enforces "only use provided context"
- [x] Source category cited in context passed to LLM
- [x] "I don't know" for low relevance scores (< 0.3) with operator offer
- [x] Retrieval works in both Turkish and English (language filter in Pinecone query)
- [x] status_check tool chaining uses live RAG instead of hardcoded data
# ISSUE-13: Build Mock Government API

## Module
Tool Calling & External Integrations

## Priority
P1

## Dependencies
ISSUE-09

## Description
Build a mock government services API that simulates real backend systems. The agent will call this API to check application status, book appointments, and request documents.

## Tasks
- [x] Build FastAPI endpoints on api/server.py (port 8001):
  - `GET /applications/{ref_number}` → application status, estimated completion, PII-safe response
  - `POST /appointments` → slot matching by preferred date, returns confirmation with date/time/office
  - `GET /appointments/{citizen_id}` → list of all appointments (confirmed, completed)
  - `POST /documents/request` → auto-generates DOC-YYYY-XXXX ref, estimated days per doc type
  - `GET /services` → 5 service catalog entries (TR + EN names/descriptions)
- [x] Appointment and DocumentRequest DB models (SQLAlchemy) with ForeignKey to Citizen
- [x] Seed data: 23 citizens (5 statuses), 5 appointments (incl. 1 completed), 3 document requests
- [x] Error responses — 404 for unknown records, 409 for no available slots
- [x] API documentation via FastAPI auto-generated Swagger UI (`/docs`)
- Skipped: response delays (unnecessary for demo), rate limiting (overkill)

### DB Schema Debt (identified post-completion):
- [x] Separate Application table from Citizen — current schema is 1:1 (one application per citizen), should be 1:N (multiple applications per citizen with service_type: passport, id_card, etc.)
- [x] Add `DELETE /appointments/{appointment_id}` endpoint for cancellation
- [x] Add `GET /documents/status/{citizen_id}` endpoint for document request tracking

## Acceptance Criteria
- [x] All endpoints return structured JSON with realistic data
- [x] Error cases return appropriate HTTP status codes (404, 409)
- [x] Swagger UI documentation auto-generated at /docs
- [x] Seed data covers edge cases (5 statuses, past appointments, multiple doc types)

---

# ISSUE-14: Implement Tool Calling in Agent

## Module
Tool Calling & External Integrations

## Priority
P1

## Dependencies
ISSUE-07, ISSUE-09, ISSUE-13

## Description
Connect the LangGraph agent to the mock government API via tool calling. The agent should autonomously decide which API to call based on the conversation context.

## Tasks
- [x] Connect LangGraph nodes to government API endpoints via httpx:
  - status_check → GET /applications/{ref} (with fallback to profile data)
  - appointment_book → POST /appointments (with fallback error message)
  - document_request → POST /documents/request (with fallback error message)
- [x] Add citizen_id to auth response profile for API calls
- [x] Parse API responses and convert to natural language (bilingual TR/EN)
- [x] Handle tool execution failures — httpx timeout/connection error → graceful fallback message
- [x] Tool chaining: status "additional_docs_needed" → auto RAG query (Pinecone)
- [x] All tool calls logged via agent.logging_config
- [x] 15 services API tests + 40 graph tests passing
- Deferred: confirmation before booking (requires multi-turn state in ISSUE-16)

### Missing Node Coverage (identified post-completion):
- [x] `appointment_list` node — call GET /appointments/{citizen_id} to show existing appointments
- [x] `appointment_cancel` node — call DELETE /appointments/{id} to cancel
- [x] `document_status` node — call GET /documents/status/{citizen_id} to check request status
- [x] Update intent_classify to support: appointment_list, appointment_cancel, document_status intents

## Acceptance Criteria
- [x] Nodes call real API endpoints when server is available
- [x] Nodes fall back gracefully when API is down (error message, not crash)
- [x] API responses converted to natural language in correct language
- [x] Tool chaining works: status → RAG docs lookup
- [x] All tool calls logged with success/failure status

---

# ISSUE-15: Edge Case Handling for Integrations

## Module
Tool Calling & External Integrations

## Priority
P2

## Dependencies
ISSUE-14

## Description
Handle edge cases and unexpected scenarios in the tool calling flow to ensure the agent behaves robustly in production-like conditions.

## Tasks

### Auth Edge Cases (LLM-based, no hardcoded keywords)
- [x] Auth refusal ("kimliğimi vermek istemiyorum") → guest mode guidance
- [x] Third-party inquiry ("arkadaşımın başvurusu") → security block
- [x] Partial TC Kimlik ("sonu 901 ile biten") → full number request
- [x] Vague date of birth ("doksanlı yıllar") → exact date request
- [x] TC Kimlik/date format normalization utilities (date_parser.py, tc_kimlik.py)

### Tool Calling Edge Cases
- [x] Past date appointment → 400 rejection with guidance
- [x] Duplicate appointment (same service, same date) → 409 with info
- [x] No available slots → 409 with "try different date"
- [x] API unavailable → graceful fallback message (httpx error handling in nodes)

### Conversation Flow Edge Cases (LLM-based)
- [x] Topic change ("bırak onu, şikayet istiyorum") → follows new topic
- [x] Ambiguous "hayır" → politely asks if anything else needed
- [x] Code-switching (TR/EN mix) → handles natively
- [x] Previous call reference → explains no prior access, helps with current need

### Meta Questions (LLM-based)
- [x] Robot/AI question → self-introduction as Umut
- [x] Capability question → brief service list
- [x] Identity question ("kimsin?") → honest AI disclosure

### Anger/Frustration (LLM-based)
- [x] Profanity → empathetic escalation to human, context-aware tone
- [x] Frustration without profanity → acknowledges, offers help

### Platform Settings
- [x] Silence timeout: 30 seconds → end call
- [x] Turn timeout: 15 seconds
- [x] Max conversation duration: 10 minutes with Turkish farewell message
- [x] Turn eagerness: patient (government service, don't interrupt)
- [x] Caller interruption: handled by ElevenLabs native barge-in

### Voice Output Quality
- [x] TTS-friendly prompt: no digits, tables, or formatting
- [x] Sentence-level SSE streaming with delays for proper TTS delivery
- [x] System prompts include voice output rules (both TR and EN)

## Acceptance Criteria
- [x] All edge cases handled by LLM prompts — zero hardcoded keyword patterns
- [x] Agent never crashes or goes silent — fallback at every level
- [x] Edge case handling feels natural — empathetic escalation, honest AI disclosure
- [x] Both TR and EN edge cases tested and working
- [x] Platform settings configured via deploy script API

---

# ISSUE-15B: Twilio Phone Integration

## Module
Voice Infrastructure

## Priority
P1

## Dependencies
ISSUE-08, ISSUE-15

## Description
Integrate Twilio to enable real phone calls to the voice agent. This transforms the demo from a browser-only widget test into a production-grade voice system where citizens dial a real phone number, get authenticated, and receive service — all over a phone call.

**FDE impact:** Showing a real phone transfer in the demo video is the difference between "I built a chatbot with voice" and "I built a production telephony system." Deutsche Telekom, Revolut, Klarna — all Oscar's reference accounts are phone-based.

## Tasks
- [ ] Create Twilio trial account and get a phone number
- [ ] Configure Twilio SIP trunk or phone number to connect to ElevenLabs agent
- [ ] Enable `system__caller_id` dynamic variable for caller identification
- [ ] Enable ENABLE_PHONE_TRANSFER=true in escalate node — real transfer to operator number
- [ ] Test full telephony flow: dial in → STT → LangGraph → TTS → voice out
- [ ] Test language detection via phone (no widget dropdown — STT auto-detects)
- [ ] Test human transfer via phone (transfer_to_number system tool)
- [ ] Measure end-to-end latency over phone vs widget

## Acceptance Criteria
- [ ] A real phone number can be dialed and reaches the voice agent
- [ ] Full conversation works over phone: greeting → intent → service → closing
- [ ] Human transfer works: agent says goodbye, call transfers to operator number
- [ ] Language detection works via audio (no manual selection needed)
- [ ] Latency is acceptable for natural phone conversation

## Notes
- Twilio trial is free — limited to verified numbers but sufficient for demo
- ElevenLabs has native Twilio integration: https://elevenlabs.io/docs/eleven-agents/customization/personalization/twilio-personalization
- This also unlocks `system__caller_id` for potential caller ID-based silent auth (future enhancement)
- Demo video should show: phone ringing → agent answers → conversation → transfer

---

# ISSUE-16: Conversation Context Management

## Module
Conversation Management

## Priority
P1

## Dependencies
ISSUE-07, ISSUE-13 (Application 1:N — DONE)

## Description
Implement dynamic context management so the agent understands the full conversation flow. Instead of looking at the last N messages statically, send the full filtered conversation history to intent_classify so the LLM can understand follow-ups, topic references, and conversation state.

**Approach:** Filtered full history (B option) — ElevenLabs sends full message history each turn. We filter out system messages (99K+ tokens) and pass all user + assistant messages to intent_classify. GPT-4o-mini's 128K context handles 10-minute conversations easily. No extra LLM call, no latency increase, works with ElevenLabs' stateless architecture.

**Reference:** LangChain Context Engineering — "write, select, compress, isolate" strategies. We use "select" (filter irrelevant system messages) rather than "compress" (summarize) to avoid extra latency in voice conversations.

## Tasks
- [x] intent_classify: send full filtered history (user + assistant messages only, no system messages) instead of last 4 messages
- [x] intent_classify prompt: update to analyze full conversation context, understand follow-ups and references
- [x] Verify auth state persistence — citizen_profile available via dynamic variables every turn
- [x] Test: "pasaport" after application listing → status_check (not faq)
- [x] Test: "randevu almak istiyorum" after status check → appointment_book (no re-auth)
- [x] Test: "az önce baktığımız başvuru" → resolves to correct application
- [x] Test: long conversation (10+ turns) quality doesn't degrade

**Note:** Implemented via full filtered conversation history + intent_classify with GPT-4o. No summarization needed — ElevenLabs sends full history each turn.

## Acceptance Criteria
- [x] Agent correctly understands follow-up responses in context (e.g., "pasaport" after listing)
- [x] Auth state persists — no re-auth request after first authentication
- [x] Entity references resolved ("my appointment" → the one just discussed)
- [x] No extra LLM call — zero latency increase vs current implementation
- [x] Works with ElevenLabs' stateless full-history architecture

## References
- https://blog.langchain.com/context-engineering-for-agents/
- https://docs.langchain.com/oss/python/langchain/context-engineering
- https://elevenlabs.io/blog/unpacking-elevenagents-orchestration-engine

---

# ISSUE-17: Multi-Intent Conversation Flows

## Module
Conversation Management

## Priority
P1

## Dependencies
ISSUE-14, ISSUE-16

## Description
Support callers who have multiple requests in a single call. The agent should handle each request sequentially and transition smoothly between them.

## Tasks
- [x] Implement intent queue — when caller mentions multiple needs, agent addresses them one by one
- [x] Build transition prompts — "I've checked your application status. You also mentioned you'd like to book an appointment — shall we do that now?"
- [x] Handle intent changes — caller starts with status check but wants to switch to appointment booking
- [x] Implement conversation summary at end of call — "Today we checked your application status and booked an appointment for Tuesday. Is there anything else?"
- [x] Track resolved vs pending intents in state

**Note:** Implemented via workflow backward edges (Service Router <-> service nodes) + LangGraph intent_classify fallback. Single conversation supports status check -> appointment -> document -> complaint transitions.

## Acceptance Criteria
- [x] Agent handles 3+ intents in a single call without confusion
- [x] Transitions between intents feel natural
- [x] End-of-call summary accurately reflects what was accomplished
- [x] Intent changes mid-flow don't break the conversation

---

# ISSUE-18: Human Handoff Implementation

## Module
Conversation Management

## Priority
P1

## Dependencies
ISSUE-06, ISSUE-07

## Description
Implement the human agent handoff flow for cases the AI agent cannot resolve. Include context transfer so the human agent knows what happened in the conversation.

## Tasks
- [ ] Define escalation triggers:
  - Caller explicitly asks for a human
  - Authentication fails 3 times
  - Agent cannot answer after 2 RAG attempts
  - Caller expresses strong frustration (sentiment detection)
  - Sensitive topics (complaints, legal issues)
- [ ] Build context handoff package — summary of conversation, caller identity, intent, what was tried
- [ ] Implement warm transfer message — agent explains to caller what will happen next
- [ ] Simulate handoff endpoint — in production this would connect to a call center, for demo purposes log the handoff event
- [ ] Track handoff rate and reasons in analytics

## Acceptance Criteria
- [ ] All escalation triggers work correctly
- [ ] Context package contains sufficient information for human agent
- [ ] Caller receives a clear, reassuring message during transfer
- [ ] Handoff events are logged with full context for analytics

---

# ISSUE-19: Streamlit Dashboard Skeleton

## Module
Analytics Dashboard

## Priority
P2

## Dependencies
None (can start in parallel with data structure defined)

## Description
Set up the Streamlit analytics dashboard with navigation, layout, and placeholder sections for all metrics.

## Tasks
- [ ] Create Streamlit app with sidebar navigation:
  - Overview (summary metrics)
  - Call Analytics (volume, duration, resolution)
  - Authentication Analytics (success/failure rates)
  - Intent Analytics (distribution, trends)
  - Language Analytics (distribution)
  - Agent Performance (latency, handoff rate)
  - Cost Analytics (per-call cost breakdown, ROI vs human agent)
- [ ] Design the layout with clean, professional styling
- [ ] Build data ingestion layer — read from log files or lightweight database (SQLite)
- [ ] Create reusable chart components (line charts, bar charts, pie charts, metric cards)
- [ ] Add date range filter and refresh functionality
- [ ] Implement auto-refresh for near-real-time updates

## Acceptance Criteria
- [ ] Dashboard loads with all navigation sections
- [ ] Layout is clean and professional
- [ ] Date range filter works across all pages
- [ ] Placeholder data renders correctly in all chart types

---

# ISSUE-20: Call Volume & Resolution Metrics

## Module
Analytics Dashboard

## Priority
P2

## Dependencies
ISSUE-19, ISSUE-14 (needs logged call data)

## Description
Implement the core call analytics metrics: volume, duration, and resolution tracking.

## Tasks
- [ ] Track and display:
  - Total calls (today, this week, this month)
  - Average call duration
  - Resolution rate (resolved by agent vs escalated to human)
  - Calls by time of day (heatmap)
  - Call volume trend (line chart over time)
- [ ] Calculate and display:
  - First-call resolution rate
  - Average turns per call
  - Average response latency
- [ ] Add comparison metrics — current period vs previous period with delta indicators
- [ ] Track and display per-call cost breakdown:
  - ElevenLabs cost (STT + TTS minutes)
  - LLM cost (input/output tokens)
  - Pinecone cost (query volume)
  - Total cost per call, per resolved case, per intent type
  - AI agent cost vs estimated human agent cost (benchmark: $4-6 per human-handled call)
  - Cumulative cost savings over time

## Acceptance Criteria
- [ ] All metrics calculate correctly from log data
- [ ] Charts render cleanly with proper labels and legends
- [ ] Comparison deltas show correct percentage changes
- [ ] Dashboard updates as new call data comes in
- [ ] Cost per call is visible and broken down by component
- [ ] ROI comparison (AI vs human agent) is displayed with clear savings percentage

---

# ISSUE-21: Intent, Language & Authentication Analytics

## Module
Analytics Dashboard

## Priority
P2

## Dependencies
ISSUE-19, ISSUE-05, ISSUE-12

## Description
Track and visualize intent distribution, language usage, and authentication performance metrics.

## Tasks
- [ ] Intent analytics:
  - Intent distribution pie chart (status check, appointment, document request, FAQ)
  - Intent trend over time
  - Resolution rate by intent type
- [ ] Language analytics:
  - Language distribution (Turkish vs English)
  - Resolution rate by language
  - Language switch frequency
- [ ] Authentication analytics:
  - Auth success rate
  - Average attempts before success
  - Failure reasons breakdown (wrong ID, wrong DOB, format error)
  - Auth failure to human handoff conversion rate
- [ ] Prompt version analytics:
  - Resolution rate per prompt version
  - Average call duration per prompt version
  - Handoff rate per prompt version
  - Side-by-side comparison view for active prompt versions

## Acceptance Criteria
- [ ] All analytics display correctly from logged data
- [ ] Cross-filtering works (e.g., filter by language and see intent distribution)
- [ ] Charts are meaningful and actionable — a product manager could make decisions from them
- [ ] Prompt version comparison helps decide which version to promote to production

---

# ISSUE-22: Anomaly Detection & Alerting

## Module
Analytics Dashboard

## Priority
P2

## Dependencies
ISSUE-20, ISSUE-21

## Description
Implement anomaly detection on key metrics to flag unusual patterns that might indicate issues.

## Tasks
- [ ] Define anomaly thresholds for key metrics:
  - Call volume spike/drop (>2x or <0.5x compared to 7-day average)
  - Authentication failure rate spike (>30% in a 1-hour window)
  - Handoff rate spike (>20% in a 1-hour window)
  - Average latency spike (>1s first-token response)
- [ ] Implement simple statistical anomaly detection (z-score or rolling average deviation)
- [ ] Build alert panel in dashboard — red/yellow/green status indicators
- [ ] Log all detected anomalies with timestamp, metric, expected value, actual value

## Acceptance Criteria
- [ ] Anomalies are correctly detected based on defined thresholds
- [ ] Alert panel clearly shows current system health
- [ ] Historical anomalies are logged and viewable
- [ ] No false positives on normal data patterns
# ISSUE-23: End-to-End Integration Testing

## Module
Testing & Quality

## Priority
P1

## Dependencies
ISSUE-08, ISSUE-14, ISSUE-12

## Description
Test the complete caller journey from initial greeting through authentication, service request, and resolution. Ensure all components work together seamlessly.

## Tasks
- [ ] Define 5 complete test scenarios:
  1. Turkish caller → authenticates with TC Kimlik → checks application status → satisfied → ends call
  2. English caller → authenticates with reference number → books appointment → confirms → ends call
  3. Turkish caller → fails authentication 3 times → transferred to human
  4. English caller → asks FAQ question (no auth needed) → gets answer from RAG → asks follow-up → ends call
  5. Turkish caller → multiple intents: checks status + books appointment + asks FAQ → summary → ends call
- [ ] Execute each scenario as a live voice call
- [ ] Log full conversation transcripts with timestamps
- [ ] Measure end-to-end latency at each stage (STT → LLM → tool call → TTS)
- [ ] Identify and document all issues found during testing
- [ ] Fix critical issues and re-test
- [ ] Build automated conversation evaluation framework:
  - Define evaluation criteria: correctness, tone, completeness, guardrail adherence, language consistency
  - Create a test suite of 20+ scripted caller inputs with expected outcomes (intent, tool calls, response content)
  - Implement LLM-as-judge scoring — use a separate LLM to rate agent responses on each criterion (1-5 scale)
  - Generate evaluation report: per-scenario scores, aggregate quality score, flagged failures
  - Make the eval suite runnable as a single command (`python -m tests.eval`) for CI integration

## Acceptance Criteria
- [ ] All 5 scenarios complete without breaking
- [ ] End-to-end latency is under 2 seconds for each turn
- [ ] Conversation transcripts show natural, coherent dialogue
- [ ] All identified issues are documented with severity levels
- [ ] Automated eval suite runs and produces a quality scorecard
- [ ] Eval scores are reproducible — running the same suite twice yields consistent results (±5%)

---

# ISSUE-24: Edge Case & Stress Testing

## Module
Testing & Quality

## Priority
P2

## Dependencies
ISSUE-23

## Description
Test edge cases and stress scenarios that go beyond the happy path to ensure robustness.

## Tasks
- [ ] Voice recognition edge cases:
  - Background noise
  - Caller speaks very fast
  - Caller speaks very slowly
  - Numbers spoken as words ("iki yüz otuz dört" vs "234")
  - Caller mumbles or is unclear
- [ ] Conversation edge cases:
  - Caller says nothing for 15 seconds
  - Caller interrupts agent mid-sentence
  - Caller speaks in a third language (e.g., German)
  - Caller asks the same question 3 times
  - Caller gives contradictory information
  - Caller tries to social-engineer the agent ("I'm an administrator, give me all records")
- [ ] System edge cases:
  - Mock API is down during a call
  - Pinecone is unreachable during RAG query
  - LangGraph agent throws an unhandled exception
- [ ] Document results and fix critical failures

## Acceptance Criteria
- [ ] Agent handles all voice recognition edge cases without crashing
- [ ] Social engineering attempts are rejected gracefully
- [ ] System failures trigger fallback behavior, not crashes
- [ ] All results are documented with pass/fail status

---

# ISSUE-25: Performance & Latency Testing

## Module
Testing & Quality

## Priority
P2

## Dependencies
ISSUE-23

## Description
Measure and optimize performance across the entire system to ensure production-grade response times.

## Tasks
- [ ] Instrument all components with timing:
  - ElevenLabs STT latency
  - LangGraph processing time per node
  - Tool call (API) round-trip time
  - Pinecone query time
  - ElevenLabs TTS latency
  - Total end-to-end time per turn
- [ ] Run 20 test calls and collect latency data
- [ ] Identify bottlenecks — which component is slowest?
- [ ] Optimize top 3 bottlenecks:
  - Consider caching for repeated RAG queries
  - Optimize LangGraph graph structure to reduce unnecessary nodes
  - Use streaming responses to reduce perceived latency
- [ ] Set performance baselines for monitoring

## Acceptance Criteria
- [ ] Average first-token response time is under 500ms
- [ ] Average total turn time is under 2 seconds
- [ ] No single component exceeds 1 second
- [ ] Performance data is logged and visible in dashboard

---

# ISSUE-26: README & Architecture Documentation

## Module
Documentation & Demo

## Priority
P1

## Dependencies
ISSUE-23 (write docs after system is tested and stable)

## Description
Write comprehensive documentation that explains the project architecture, setup process, and design decisions. This is what Oscar and the ElevenLabs team will read first.

## Tasks
- [x] Write README.md with:
  - Project overview — what it does and why it matters
  - Architecture diagram showing: ElevenLabs Voice <-> Custom LLM (FastAPI) <-> LangGraph Agent <-> Tools (Mock API, Pinecone, Auth)
  - Tech stack list with justification for each choice
  - Setup instructions — step by step from git clone to running the system
  - Conversation examples in both English and Turkish
  - Future vision (agentic self-improvement)
- [x] Write ARCHITECTURE.md with:
  - System overview (Mermaid graph)
  - Request lifecycle sequence diagram
  - Deterministic tool chaining diagram
  - ElevenLabs Workflow diagram
  - LangGraph node graph
  - Analytics pipeline
  - Database ER diagram
- [x] Write DECISIONS.md with:
  - 13 key design decisions with rationale and tradeoffs
  - Why LangGraph + Workflow split
  - Why stateless, why GPT-4o, why STT-friendly auth
  - Production scalability considerations

## Acceptance Criteria
- [x] A developer can set up and run the project by following README alone
- [x] Architecture is clearly explained with visual diagrams
- [x] Design decisions demonstrate strategic thinking, not just implementation
- [x] Documentation quality reflects enterprise readiness

---

# ISSUE-27: Video Demo Recording

## Module
Documentation & Demo

## Priority
P0 — This is what gets you the interview

## Dependencies
ISSUE-23, ISSUE-26

## Description
Record a polished video walkthrough demonstrating the full system. This is the most important deliverable — Oscar specifically said "we love seeing candidates come in with something tangible."

## Tasks
- [x] Plan the demo script — 8 independent segments, all English:
  - A. (30-45s) Introduction — face camera, problem + solution
  - B. (60s) Architecture walkthrough — screen share ARCHITECTURE.md
  - C. (30-45s) Live demo: Pre-auth FAQ — native KB, no auth needed
  - D. (60-90s) Live demo: Auth + Status Check — full auth flow + multi-app listing
  - E. (45-60s) Live demo: Appointment booking — service type detection, multi-intent
  - F. (30-45s) Live demo: Topic change — complaint as third intent
  - G. (45-60s) Dashboard walkthrough — overview, flows, node details
  - H. (30s) Closing — face camera, scalability + future vision
- [x] Write detailed script with talking points (docs/demo_script.md)
- [x] Include contingency plans for common failures
- [ ] Set up clean screen recording environment (screen + face cam)
- [ ] Do 2-3 practice runs before final recording
- [ ] Record each segment independently
- [ ] Edit and combine segments
- [ ] Upload to YouTube (unlisted) and link in README

## Acceptance Criteria
- [ ] Video is 5-6 minutes
- [ ] Audio is clear, pacing is confident, no major fumbles
- [ ] Live demo calls work end-to-end without errors
- [ ] Dashboard shows real metrics from the demo calls
- [ ] Architecture explanation demonstrates deep understanding, not just usage
- [ ] Video makes the viewer think "this person could do this for our enterprise customers"

---

# ISSUE-28: Application Summary Document

## Module
Documentation & Demo

## Priority
P1

## Dependencies
ISSUE-27

## Description
Prepare a concise written summary that can be shared alongside the video and repo. This is the executive-level companion to the technical demo.

## Tasks
- [x] Write a 1-page summary covering:
  - Problem statement — why government services need AI voice agents
  - Solution overview — what you built and how it works (7 capabilities table)
  - Architecture highlights — Workflow + Custom LLM + degradation + dashboard
  - Key metrics — latency, languages, nodes, KB size, test coverage
  - Why ElevenLabs — 6 platform features mapped to usage
  - Enterprise scalability — stateless, multi-tenant, compliant, observable
  - ROI potential — cost reduction, 24/7 service, wait time elimination
- [ ] Format as clean PDF
- [ ] Include architecture diagram and 2-3 dashboard screenshots

## Acceptance Criteria
- [x] A non-technical stakeholder can understand the value proposition
- [x] Metrics are concrete and credible
- [x] ElevenLabs platform advantages are highlighted naturally, not forced
- [x] Summary makes the reader want to see the full demo
- [ ] Formatted as clean PDF with visuals

---

# ISSUE-29: DTMF Numpad Authentication

## Module
Voice Infrastructure

## Priority
P2

## Dependencies
ISSUE-15B (Twilio)

## Description
Allow callers to enter TC Kimlik last 4 digits via phone numpad (DTMF tones) instead of speaking them aloud. This eliminates STT misrecognition of numbers and provides a more familiar, secure experience — similar to how banks handle PIN entry over phone.

**FDE impact:** Shows deep understanding of telephony UX and enterprise voice security patterns. Banks, insurance companies, and government call centers all use DTMF for sensitive data entry.

## Tasks
- [ ] Research Twilio DTMF gather integration with ElevenLabs (Twilio `<Gather>` verb or ElevenLabs native DTMF input)
- [ ] Implement DTMF collection endpoint — Twilio sends gathered digits to our webhook
- [ ] Integrate with auth webhook — DTMF digits used as tc_kimlik_last4 parameter
- [ ] Agent prompts user: "Lutfen TC Kimlik numaranizin son dort hanesini telefonunuzun tuslarindan giriniz"
- [ ] Fallback: if DTMF not received within timeout, fall back to voice input
- [ ] Test with real phone call via Twilio

## Acceptance Criteria
- [ ] Caller can enter 4 digits via numpad during a live call
- [ ] Digits are correctly captured and sent to auth webhook
- [ ] Fallback to voice input works when numpad not used
- [ ] Experience feels natural — similar to banking IVR systems
- **Status:** Blocked — ElevenLabs does not support receiving DTMF input from callers. `play_keypad_touch_tone` only sends tones, cannot receive them.

---

# ISSUE-36: Dead Code Cleanup

## Module
Code Quality

## Priority
P2

## Dependencies
None

## Description
Identify and remove dead code, unused imports, debug artifacts, and stale references across the codebase. Ensure no orphaned files or unreachable code paths remain.

## Tasks
- [x] Scan for unused imports across all Python files
- [x] Remove data/last_system_prompt.txt debug logging from server.py
- [x] Verify auth_collect.py is fully removed (no stale references)
- [x] Check for unreachable code paths in graph.py (e.g., workflow_node routing)
- [x] Remove build_auth_workflow() from deploy.py (165 lines, never called)
- [x] Remove dead Pydantic classes + TOOL_SCHEMAS + get_all_tool_schemas() from schemas.py
- [x] Remove unused timer() from utils.py
- [x] Clean test_schemas.py to match slimmed schemas.py
- [x] Remove any hardcoded test values or TODO comments (none found)
- [x] Verify all imports in __init__.py files (all clean)

## Acceptance Criteria
- [x] No unused imports
- [x] No debug artifacts in production code
- [x] No orphaned files
- [ ] All tests still pass after cleanup

---

# ISSUE-30: LLM-Powered Dashboard Insights

## Module
Analytics Dashboard

## Priority
P1

## Dependencies
ISSUE-19, ISSUE-20

## Description
Use a powerful LLM (GPT-4o / Claude) to analyze dashboard metrics periodically and generate actionable recommendations. Transforms the dashboard from a metric display into a decision support system.

**FDE impact:** No competitor does this — most dashboards show numbers, ours tells you what to do about them.

## Tasks
- [ ] Build insight generation endpoint — sends aggregated metrics to LLM, returns recommendations
- [ ] Implement periodic analysis (cron-style or on-demand)
- [ ] Display insights in dashboard with timestamp and confidence
- [ ] Example insights: "Escalation rate increased 40% this week, mostly after status_check. Review status check responses." or "FAQ about passport documents has 3x more queries — consider adding more detail to knowledge base."

## Acceptance Criteria
- [ ] LLM generates actionable, specific insights from real metrics
- [ ] Insights are displayed in dashboard with clear formatting
- [ ] Recommendations are business-focused, not technical jargon

---

# ISSUE-31: A/B Testing Framework

## Module
Analytics Dashboard

## Priority
P2

## Dependencies
ISSUE-19

## Description
Enable comparison of different prompt versions, LLM models, or conversation strategies. Track resolution rate, response time, and escalation rate per variant. Data-driven optimization for voice agent performance.

## Tasks
- [ ] Add variant tracking to ConversationLog (prompt_version, model_name fields)
- [ ] Dashboard A/B comparison view — side-by-side metrics per variant
- [ ] Statistical significance indicator
- [ ] Example: "GPT-4o prompt v1.0 vs v1.1: resolution rate 85% vs 92%, p-value 0.03"

## Acceptance Criteria
- [ ] Variant metrics tracked per conversation
- [ ] Dashboard shows side-by-side comparison with key metrics
- [ ] Clear recommendation on which variant performs better

---

# ISSUE-32: Sentiment Tracking

## Module
Analytics Dashboard

## Priority
P1

## Dependencies
ISSUE-19

## Description
Analyze conversation sentiment in real-time. Track how citizens' mood changes during the call — started angry, ended satisfied. Display sentiment trends in dashboard.

## Tasks
- [ ] Add sentiment analysis to each conversation turn (LLM-based or lightweight model)
- [ ] Store sentiment per conversation in ConversationLog
- [ ] Dashboard: sentiment distribution chart, sentiment trend over time
- [ ] Dashboard: "citizen satisfaction proxy" — percentage of conversations ending positive

## Acceptance Criteria
- [ ] Sentiment tracked per conversation
- [ ] Dashboard shows sentiment distribution and trends
- [ ] Actionable: identifies which intents/flows produce negative sentiment

---

# ISSUE-33: Conversation Flow Visualization

## Module
Analytics Dashboard

## Priority
P1

## Dependencies
ISSUE-19

## Description
Visualize conversation paths as flow diagrams. Show the most common paths citizens take (Auth → Status → Appointment), drop-off points, and bottlenecks.

## Tasks
- [ ] Track node transitions per conversation
- [ ] Build Sankey diagram or flow chart in dashboard
- [ ] Identify most common paths and drop-off points
- [ ] Highlight bottleneck nodes (high response time or escalation)

## Acceptance Criteria
- [ ] Flow visualization shows real conversation paths
- [ ] Most common paths clearly visible
- [ ] Drop-off and bottleneck nodes identified

---

# ISSUE-34: Knowledge Gap Detection

## Module
Analytics Dashboard

## Priority
P1

## Dependencies
ISSUE-12, ISSUE-19

## Description
Automatically detect questions that the knowledge base cannot answer well. Collect low-score RAG queries and present them as content improvement recommendations.

## Tasks
- [ ] Log RAG query scores in ConversationLog or separate table
- [ ] Dashboard: "Knowledge Gaps" section — queries with low relevance scores
- [ ] Group similar unanswered queries by topic
- [ ] Generate content recommendations: "Add more detail about ehliyet yenileme — 12 queries, avg score 0.25"

## Acceptance Criteria
- [ ] Low-score RAG queries collected and displayed
- [ ] Grouped by topic for easy action
- [ ] Content team can see exactly what to add

---

# ISSUE-35: Peak Hour Prediction & Cost Optimizer

## Module
Analytics Dashboard

## Priority
P3 — Deferred (requires 30+ days of production data)

## Dependencies
ISSUE-19, ISSUE-20

## Description
Predict peak call hours from historical data and calculate per-intent cost breakdown. Both sections show placeholder UI with "production data needed" note until sufficient data is collected.

## Tasks
- [ ] Dashboard: Peak hour prediction section with placeholder chart
- [ ] Dashboard: Cost per intent breakdown with placeholder data
- [ ] Add note: "Requires 30+ days of production data for accurate predictions"
- [ ] Design data collection structure for future activation

## Acceptance Criteria
- [ ] UI sections visible in dashboard with professional layout
- [ ] Clear "production data needed" messaging
- [ ] Data structure ready for future activation

---

# ISSUE-37: Multi-Agent Insight Engine (Redesign)

## Module
Analytics Dashboard — Intelligence Layer

## Priority
P3 — Deferred (requires production data volume to be meaningful)

## Dependencies
ISSUE-30 (current basic version)

## Description
Redesign the LLM-powered insight engine with specialized agents. Each agent focuses on one domain, produces specific actionable recommendations with source data attribution. Current single-LLM approach produces generic advice — multi-agent approach produces expert-level, platform-aware recommendations.

**Why multi-agent:** Each analysis domain (RAG quality, node performance, escalation patterns) requires different context, different metrics, and different ElevenLabs documentation. Single prompt can't do justice to all three.

**Why deferred:** Without real production conversation data, even 5 specialized agents will produce generic recommendations. This becomes valuable only after sufficient data volume exists.

## Architecture
```
Orchestrator Agent (GPT-4o)
  → RAG Quality Agent — analyzes rag_score, rag_query, knowledge gaps
  → Performance Agent — analyzes response_time, intent_classify_ms, bottlenecks
  → Pattern Agent — analyzes escalation rate, intent transitions, failure patterns
  → Platform Agent — retrieves ElevenLabs docs RAG, maps findings to platform features
  → Report Generator — combines all agent outputs into prioritized recommendations
```

## Tasks
- [ ] Design agent architecture — which agents, what each one receives/returns
- [ ] RAG Quality Agent — low score analysis, content gap grouping, specific document recommendations
- [ ] Performance Agent — per-node timing, bottleneck detection, model/config optimization suggestions
- [ ] Pattern Agent — escalation trigger analysis, intent flow failures, conversation drop-off points
- [ ] Platform Agent — ElevenLabs docs RAG, map each finding to specific platform feature/config
- [ ] Report Generator — deduplicate, prioritize, format with source data attribution
- [ ] Source data attribution — every recommendation shows which metrics/data it's based on
- [ ] Dashboard integration — replace current "Generate Insights" with richer multi-agent output

## Acceptance Criteria
- [ ] Each recommendation references specific metrics ("avg_response=6502ms, bottleneck: faq_answer")
- [ ] Each recommendation suggests specific platform action ("enable ElevenLabs buffer words" not "reduce latency")
- [ ] Source data visible for every insight
- [ ] Recommendations are non-generic — could not be generated without looking at actual data

---

# ISSUE-38: Enhanced ElevenLabs Docs RAG

## Module
RAG Pipeline

## Priority
P3 — Deferred (depends on ISSUE-37, same production data constraint)

## Dependencies
ISSUE-37

## Description
Improve the ElevenLabs documentation RAG for the insight engine. Current implementation uses a single generic query. Enhanced version uses metric-specific queries and returns more targeted documentation.

## Tasks
- [ ] Metric-aware query generation — "high escalation rate" → search for escalation handling docs
- [ ] Multi-query retrieval — each insight type generates its own focused query
- [ ] Source attribution — show which ElevenLabs doc section each recommendation comes from
- [ ] Chunk quality improvement — better splitting for ElevenLabs docs (code blocks, tabs, etc.)
- [ ] Relevance scoring — filter out low-relevance chunks before sending to LLM

## Acceptance Criteria
- [ ] RAG returns platform-specific, actionable documentation
- [ ] Each insight cites specific ElevenLabs doc section
- [ ] No generic "visit the docs" recommendations

---

# ISSUE-39: Sentiment Tracking Implementation

## Module
Analytics Dashboard

## Priority
P3 — Deferred (requires production conversation volume for meaningful sentiment analysis)

## Dependencies
ISSUE-19

## Description
Analyze conversation sentiment per turn and per conversation. Track citizen satisfaction proxy — did the conversation end positively?

## Tasks
- [ ] Add sentiment field to ConversationLog (positive/neutral/negative)
- [ ] Lightweight sentiment analysis per conversation (LLM-based, post-conversation)
- [ ] Dashboard: sentiment distribution, trend over time
- [ ] Dashboard: "citizen satisfaction proxy" — % conversations ending positive
- [ ] Correlation: which intents produce negative sentiment

## Acceptance Criteria
- [ ] Sentiment tracked per conversation
- [ ] Dashboard shows actionable sentiment insights

---

# ISSUE-40: Conversation Flow Visualization

## Module
Analytics Dashboard

## Priority
P1

## Dependencies
ISSUE-19

## Description
Visualize conversation node paths as flow diagrams. Identify common paths, drop-offs, bottlenecks.

## Tasks
- [x] Track node transitions per conversation (intent sequence)
- [x] Build Sankey or flow visualization in Streamlit
- [x] Identify most common paths and drop-off points
- [x] Highlight bottleneck nodes (high latency or escalation)

## Acceptance Criteria
- [x] Real conversation paths visible as flow diagram
- [x] Drop-off and bottleneck nodes identified

---

# ISSUE-41: Test Suite Consolidation

## Module
Testing & Quality

## Priority
P2

## Dependencies
None

## Description
The test suite has significant overlap between `test_graph.py` and `test_core_system.py`. Both files test the same nodes, routing logic, SSE proxy, and circuit breaker. Consolidate into a single organized test suite to eliminate redundancy and standardize on English-only tests.

### Overlapping areas:
- **Intent classification**: 5 tests in test_graph.py duplicated in test_core_system.py
- **Status check node**: 5 tests in test_graph.py covered more thoroughly in test_core_system.py
- **Appointment book**: 2 tests duplicated
- **Escalate**: 4 tests (tr/en, demo/production, completed_intents) overlap
- **Graph routing**: route_by_intent + entry_router duplicated
- **Full graph flows**: 3 tests duplicated
- **SSE proxy**: 7+ tests overlap
- **Circuit breaker**: 5 tests fully duplicated

## Tasks
- [x] Audit all test files for overlap (test_graph.py, test_core_system.py, test_edge_cases.py, test_services.py, etc.)
- [x] Merge unique tests from test_graph.py into test_core_system.py (Turkish escalation + SSE role check)
- [x] Remove test_graph.py after migration
- [x] Standardize all tests on English language
- [x] Ensure no coverage loss after consolidation
- [x] Update test count in CLAUDE.md

## Acceptance Criteria
- [x] No duplicate test cases across files
- [x] All unique test scenarios preserved
- [x] Single comprehensive test file per concern (core system, API services, auth, etc.)
- [x] Total test count stable or improved after cleanup

---

# ISSUE-42: Dashboard Sidebar Navigation

## Module
Analytics Dashboard

## Priority
P1

## Dependencies
ISSUE-19

## Description
Restructure dashboard from single-page tab layout to multi-page sidebar navigation. Each analytics section becomes its own page accessible from the left sidebar menu.

## Tasks
- [x] Convert 8 tabs to 8 page functions with sidebar radio navigation
- [x] Move date range filter and auto-refresh to sidebar below navigation
- [x] Add `@st.cache_data` for data loading (30s TTL)
- [x] Shared `get_filtered_data()` helper for all pages

## Acceptance Criteria
- [x] Sidebar shows all pages with clean navigation
- [x] Date filter persists across page switches
- [x] Each page loads independently
- [x] No duplicate set_page_config calls

---

# ISSUE-43: LangGraph Node Details Page

## Module
Analytics Dashboard

## Priority
P1

## Dependencies
ISSUE-42

## Description
Add detailed LangGraph node analytics page to dashboard. Track per-node performance, tool chain triggers, and API call counts. Requires new fields in ConversationLog.

## Tasks
- [x] Add fields to ConversationLog: service_node_name, tool_chain_triggered, api_calls_count
- [x] Update server.py to extract these from graph result
- [x] New dashboard page: Node Details — per-node timing, success/error, RAG chain rate, API calls
- [x] Integrate into sidebar navigation

## Acceptance Criteria
- [x] Each LangGraph node has per-node metrics visible
- [x] Tool chain triggers tracked (e.g. status → RAG)
- [x] API call counts per node visible
- [x] Page accessible from sidebar

---

# ISSUE-44: Protect Government API Routes

## Module
Security

## Priority
P0 (high severity)

## Dependencies
ISSUE-13, ISSUE-14

## Description
agent/server.py mounts the government API routers on the same public app as the Custom LLM endpoint, and ngrok exposes that app. No route checked who was calling: anyone with the URL could read any citizen's applications, appointments and document requests, book or cancel appointments, request documents, erase a citizen (DELETE /citizens/{id}), or write fake handoff events into the audit log.

Fix: shared-secret header check. Citizen-data routes require X-Internal-Key (INTERNAL_API_KEY), sent only by LangGraph nodes. GDPR erasure requires a separate X-Admin-Key (ADMIN_API_KEY) so the voice flow can never call it. Keys are compared with hmac.compare_digest; an unset key refuses every request (fail closed).

## Tasks
- [x] api/security.py — `require_internal_key` / `require_admin_key` dependencies (403 on missing/wrong key, fail closed when env var unset)
- [x] Router-level dependency on every route in api/services.py and api/handoff.py, except GET /services, GET /appointments/slots/{service_type}, POST /guest/info (split into public_router / guest_router)
- [x] DELETE /citizens/{citizen_id} moved to admin_router guarded by ADMIN_API_KEY
- [x] Shared `gov_api_request()` helper in agent/nodes/utils.py — all 7 API-calling nodes (status_check, appointment_book, appointment_list, appointment_cancel, document_request, document_status, escalate) send X-Internal-Key through it
- [x] New routers mounted in both agent/server.py and api/server.py; startup warning when INTERNAL_API_KEY is unset
- [x] INTERNAL_API_KEY= and ADMIN_API_KEY= added to .env.example
- [ ] Optional (stronger): stop exposing the government API on the public app — call service functions in-process from nodes, or run api/server.py on localhost only

## Acceptance Criteria
- [x] Existing tests in test_services.py, test_handoff.py, test_core_system.py send the internal key and still pass
- [x] Each protected route returns 403 without the key and with a wrong key
- [x] DELETE /citizens/{citizen_id} returns 403 with only the internal key, works with the admin key
- [x] The three read-only routes answer without a key
- [x] The public app (agent/server.py) enforces the same rules
- [x] Every node helper passes the key check end-to-end (tests/test_api_security.py, 60 tests)
