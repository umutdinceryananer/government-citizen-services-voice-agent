# Caller Identity Verification Flow Design

## Architecture: Workflow-Based Deterministic Authentication

Authentication is **not** left to LLM judgment. Following ElevenLabs' best practices for secure caller identity authentication, we use the platform's native **Workflows** system to enforce deterministic gating.

Reference: [Designing Secure Caller Identity Authentication Flows for Voice Agents](https://elevenlabs.io/blog/designing-secure-caller-identity-authentication-flows-for-voice-agents)

### Why Workflow-Based (Not Conversational)

| Approach | Risk | Our Choice |
|----------|------|:---:|
| LLM decides if user is authenticated | Prompt injection, hallucination, inconsistent gating | |
| **Dispatch tool + workflow edges** | Deterministic: tool returns boolean, workflow routes accordingly | **Yes** |

### Workflow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    ElevenLabs Workflow                       │
│                                                             │
│  ┌─────────────────────┐                                    │
│  │  SUBAGENT 1          │                                   │
│  │  Greeting + Collect  │                                   │
│  │                      │                                   │
│  │  - Greet caller      │                                   │
│  │  - Detect language   │                                   │
│  │  - KVKK consent      │                                   │
│  │  - Identify intent   │                                   │
│  │  - Collect auth data │                                   │
│  │                      │                                   │
│  │  Tools: NONE         │                                   │
│  │  (no privileged      │                                   │
│  │   access)            │                                   │
│  └──────────┬───────────┘                                   │
│             │                                               │
│             │ LLM Condition: "User provided credentials"    │
│             ▼                                               │
│  ┌─────────────────────┐                                    │
│  │  DISPATCH TOOL       │                                   │
│  │  verify_identity     │                                   │
│  │                      │                                   │
│  │  Input:              │                                   │
│  │  - tc_kimlik         │                                   │
│  │  - date_of_birth     │                                   │
│  │  OR                  │                                   │
│  │  - app_ref_number    │                                   │
│  │  - last_name         │                                   │
│  │                      │                                   │
│  │  Output:             │                                   │
│  │  - is_error: bool    │                                   │
│  │  - citizen_profile   │                                   │
│  │  - error_message     │                                   │
│  └──────┬───────┬───────┘                                   │
│         │       │                                           │
│   SUCCESS│     FAILURE                                      │
│         │       │                                           │
│         ▼       ▼                                           │
│  ┌────────────┐ ┌─────────────────┐                        │
│  │ SUBAGENT 2 │ │ RETRY LOGIC     │                        │
│  │ Authenticated│ │                │                        │
│  │            │ │ attempt < 3:    │                        │
│  │ Tools:     │ │  → back to      │                        │
│  │ - status   │ │    Subagent 1   │                        │
│  │ - appoint. │ │                 │                        │
│  │ - document │ │ attempt >= 3:   │                        │
│  │ - complaint│ │  → human        │                        │
│  │ - RAG      │ │    transfer     │                        │
│  │            │ └────────┬────────┘                        │
│  │ Knowledge: │          │                                  │
│  │ - Full KB  │          ▼                                  │
│  │            │ ┌─────────────────┐                        │
│  └────────────┘ │ TRANSFER NODE   │                        │
│                 │ Human operator  │                        │
│                 └─────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Subagent 1 has NO tools** — Cannot access citizen data, cannot perform actions. Only collects credentials.
2. **Dispatch tool is deterministic** — Returns `is_error: true/false`. Workflow routes based on this, not LLM opinion.
3. **Subagent 2 only reachable via success edge** — No prompt injection can bypass this gate.
4. **Retry counter tracked via dynamic variable** — `auth_attempt_count` incremented on each failure, expression edge checks `auth_attempt_count >= 3`.

## Verification Webhook (Implemented)

`POST /auth/verify/webhook` is the only auth endpoint. The dispatch tool sends the last 4 TC Kimlik digits, date of birth and father's name initial. The server:

- **Answers only the platform** — requires `Authorization: Bearer <PLATFORM_WEBHOOK_SECRET>` on every `/auth` route (403 otherwise)
- **Limits attempts server-side** — 3 failures per `conversation_id` and per keyed hash of `caller_number` within 15 minutes, then 429. In-memory and per process (demo); production needs a shared store such as Redis
- **Reveals nothing** — one generic failure message (TR/EN) whether the ID is unknown, the date is wrong or the initial is wrong
- **Audits every attempt** — `auth_audit_log` row with conversation id, attempt number, outcome and category (`format`, `no_match`, `locked`), plus the keyed hash of the citizen id on success. Never the factors
- **Returns a signed session token** — `session_token` (agent/session_token.py: `<citizen_id>.<expires_unix>.<hex HMAC-SHA256>`, 15 minutes) next to `citizen_profile`

| Status | Meaning |
|--------|---------|
| 200 | Verified — `is_error: false`, `citizen_profile`, `session_token` |
| 401 | Any verification failure — one generic message |
| 429 | Attempt limit reached for this conversation or caller number |
| 403 | Missing or wrong platform secret |

### ElevenLabs Dispatch Tool Configuration (manual, in the dashboard)

1. **Header:** `Authorization: Bearer <PLATFORM_WEBHOOK_SECRET>`. Store the value as a workspace secret, or as a dynamic variable with the `secret__` prefix — whichever the tool editor offers for headers. Secret variables are used in headers only and are never sent to the LLM.
2. **Body fields:** `conversation_id` from `{{system__conversation_id}}`, `caller_number` from `{{system__caller_id}}`. Optional: `language` (`tr` / `en`, LLM-filled from the conversation; defaults to `tr`) so failure messages match the call language.
3. **Assignment:** `response.session_token` → dynamic variable `session_token`.

### Custom LLM Configuration (manual, in the dashboard)

The Custom LLM endpoint (`/v1/chat/completions`) trusts only the session token for identity — never names, IDs or application data in the prompt.

1. **API key:** in the agent's custom LLM settings, set the API key to a workspace secret holding `CUSTOM_LLM_SECRET`. The server expects `Authorization: Bearer <value>` and returns 401 otherwise. If real calls are rejected, the server's warning log lists the header names the platform sent (never values).
2. **Prompt line:** in the prompt of every custom LLM node after authentication, add `SESSION_TOKEN={{session_token}}`. Use the normal dynamic variable, not `secret__` — the token has to reach the server through the prompt. Leave it out of nodes that use a native LLM, which would send it to that model provider.
3. Prompt lines showing the name or application data may stay for speech; the server no longer uses them for identity.
4. **Re-check after `python -m agent.deploy`:** the deploy script writes the custom LLM block with only `url` and `model`, which may clear the API key set in the dashboard.

## Authentication Methods (original design)

> The tc-kimlik (Method A) and app-ref (Method B) endpoints were removed: the voice flow uses the webhook above (last 4 digits + DOB + father initial) and never called them. The design notes below are kept for reference.

### Method A: TC Kimlik + Date of Birth (Primary)

| Field | Format | Validation |
|-------|--------|------------|
| TC Kimlik No | 11 digits | Checksum algorithm (see below) + DB lookup |
| Date of Birth | DD/MM/YYYY or spoken ("15 Mart 1990") | Valid date + matches DB record |

**TC Kimlik Checksum Algorithm:**
- First digit cannot be 0
- Sum of digits at odd positions (1,3,5,7,9) × 7 minus sum of digits at even positions (2,4,6,8) → mod 10 = digit 10
- Sum of digits 1-10 → mod 10 = digit 11
- This is validated **before** hitting the database — rejects obviously invalid numbers instantly

### Method B: Application Reference Number + Last Name (Secondary)

| Field | Format | Validation |
|-------|--------|------------|
| Application Ref | Format: `YYYY-TR-NNNN` | Regex format check + DB lookup |
| Last Name | Text | Case-insensitive match against DB |

### Collection Strategy

Fields are collected **one by one**, not all at once:

```
Agent: "Kimliğinizi doğrulamam gerekiyor. TC Kimlik numaranızı
        söyleyebilir misiniz?"
User:  "12345678901"
Agent: "Teşekkürler. Doğum tarihinizi de alabilir miyim?"
User:  "15 Mart 1990"
Agent: (verify_identity tool call happens here)
```

**Why one by one:**
- Voice input is error-prone — collecting all at once leads to misheard digits
- Agent can validate format after each field and ask for correction immediately
- Better UX — feels like a natural conversation, not a form

## KVKK Compliance

### Consent Notice (Before Data Collection)

```
Turkish:
"Bu görüşme kalite ve güvenlik amacıyla kaydedilmektedir.
 Kişisel verileriniz 6698 sayılı KVKK kapsamında işlenmektedir.
 Devam ederek kişisel verilerinizin işlenmesini kabul etmiş olursunuz."

English:
"This call is recorded for quality and security purposes.
 Your personal data is processed in accordance with Turkish Data
 Protection Law (KVKK, Law No. 6698). By continuing, you consent
 to the processing of your personal data."
```

**When:** Delivered once, at the start of authentication flow (Subagent 1), before any personal data is collected.

### Data Handling Rules

| Data | Logged | Stored | Retention |
|------|:------:|:------:|-----------|
| TC Kimlik | Masked (123****901) | Keyed hash (HMAC-SHA256, ID_HASH_PEPPER) | 30 days |
| Date of Birth | Masked (**/**/1990) | Hashed | 30 days |
| Application Ref | Masked (2024-TR-****) | Plain | 30 days |
| Last Name | First letter only (Y***) | Hashed | 30 days |
| Auth result (pass/fail) | Full | Full | 90 days |
| Conversation transcript | PII-redacted | PII-redacted | 90 days |

### Audit Trail

Every auth attempt generates an audit record:

```json
{
  "timestamp": "2026-04-02T18:30:00Z",
  "session_id": "conv_abc123",
  "event": "auth_attempt",
  "method": "tc_kimlik_dob",
  "attempt_number": 1,
  "result": "success",
  "citizen_id_hash": "hmac-sha256:a1b2c3...",
  "ip_or_caller_id": "redacted"
}
```

No PII in audit records — only hashed identifiers.

## Security Boundaries

### What Agent CAN Say

- First name after authentication: "Hoş geldiniz, Ahmet Bey."
- Last 3 digits of TC Kimlik for confirmation: "Sonu 901 ile biten TC Kimlik numaranız, doğru mu?"
- Application status, appointment details, general info

### What Agent CANNOT Say

- Full TC Kimlik number — NEVER repeat back
- Full date of birth — NEVER repeat back
- Other citizens' data — even if asked
- Application outcome guarantees — "approved" vs "under review"
- Legal advice about rejected applications

### What Agent CANNOT Do

- Proceed to privileged tools without successful auth (enforced by workflow, not prompt)
- Store raw TC Kimlik in any log (enforced by PII redaction filter)
- Skip KVKK consent notice
- Allow more than 3 auth attempts

## Retry and Failure Handling

```
Attempt 1 (fail):
  "Girdiğiniz bilgiler eşleşmedi. Lütfen tekrar deneyin.
   TC Kimlik numaranızı söyleyebilir misiniz?"

Attempt 2 (fail):
  "Maalesef bilgiler yine eşleşmedi. Son bir deneme hakkınız var.
   Dilerseniz başvuru numaranız ile de doğrulama yapabiliriz."

Attempt 3 (fail):
  "Üzgünüm, kimliğinizi doğrulayamadım. Güvenliğiniz için sizi
   bir müsait operatöre bağlıyorum. Lütfen bekleyin."
  → Transfer to human operator
```

### Progressive Guidance

Each retry gives more specific help:
1. First failure: Simple "try again"
2. Second failure: Offer alternative method (switch from TC Kimlik to Application Ref)
3. Third failure: Automatic human transfer — no more retries

## Voice-Specific Edge Cases

| Edge Case | Handling |
|-----------|----------|
| Numbers misheard ("bir iki üç" vs "123") | Agent confirms: "12345678901, doğru mu?" |
| Caller says TC Kimlik with spaces | Strip whitespace, validate |
| Caller gives 10 or 12 digits | "TC Kimlik numarası 11 haneli olmalı, tekrar söyleyebilir misiniz?" |
| Caller gives DOB in wrong format | Agent asks: "Hangi yıl doğduğunuzu söyleyebilir misiniz?" |
| Caller refuses to give TC Kimlik | Offer Method B (Application Ref + Last Name) |
| Caller asks why auth is needed | Explain: "Kişisel bilgilerinize erişmek için kimliğinizi doğrulamam gerekiyor." |
