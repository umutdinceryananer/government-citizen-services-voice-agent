# Architecture

Detailed system design for the Government Citizen Services Voice Agent.

## System Overview

The system consists of four layers: Voice (ElevenLabs), Intelligence (LangGraph), Backend (FastAPI + SQLite), and Analytics (Streamlit).

```mermaid
graph TB
    subgraph "Citizen"
        Phone[Phone / Browser Widget]
    end

    subgraph "ElevenLabs Platform"
        STT[STT - Speech to Text]
        TTS[TTS - Text to Speech]
        WF[Workflow Engine]
        KB[Native Knowledge Base]
        LD[Language Detection]
    end

    subgraph "Custom LLM Server :8080"
        Proxy[SSE Proxy - server.py]
        CB[Circuit Breaker]
        Logger[Analytics Logger]
    end

    subgraph "LangGraph Agent"
        ER[Entry Router]
        IC[Intent Classify - GPT-4o]
        SR[Service Router]
        SC[Status Check]
        AB[Appointment Book]
        AL[Appointment List]
        AC[Appointment Cancel]
        DR[Document Request]
        DS[Document Status]
        FAQ[FAQ Answer]
        CMP[Complaint]
        ESC[Escalate]
    end

    subgraph "Data Layer"
        DB[(SQLite DB)]
        PC[(Pinecone Vector Store)]
    end

    subgraph "Dashboard"
        ST[Streamlit - 9 Pages]
    end

    Phone --> STT --> WF
    WF --> KB
    WF -->|Custom LLM| Proxy
    Proxy --> CB --> ER
    ER --> IC --> SR
    SR --> SC & AB & AL & AC & DR & DS & FAQ & CMP & ESC
    SC & AB & AL & AC & DR & DS -->|httpx| DB
    SC & FAQ -->|RAG| PC
    ESC -->|handoff log| DB
    Proxy --> Logger --> DB
    TTS --> Phone
    Proxy -->|SSE stream| TTS
    ST --> DB
```

## Request Lifecycle

What happens when a citizen says "I want to check my application status":

```mermaid
sequenceDiagram
    participant C as Citizen
    participant EL as ElevenLabs
    participant WF as Workflow
    participant CLM as Custom LLM Proxy
    participant LG as LangGraph
    participant API as Gov API
    participant RAG as Pinecone RAG
    participant DB as SQLite

    C->>EL: "I want to check my application status" (voice)
    EL->>EL: STT converts speech to text

    Note over WF: Pre-Auth Phase
    WF->>WF: Collect Identity node (GPT-4o-mini)
    WF-->>C: "Last 4 digits of your ID?"
    C->>WF: "0018"
    WF-->>C: "Date of birth?"
    C->>WF: "March 15, 1990"
    WF-->>C: "Father's name initial?"
    C->>WF: "M"

    WF->>API: POST /auth/verify/webhook
    API->>DB: Query citizens (DOB + father initial + last4)
    DB-->>API: Citizen found (Ahmet, id=1)
    API-->>WF: 200 OK {first_name: "Ahmet", citizen_id: 1}

    Note over WF: Post-Auth Phase
    WF->>WF: Service Router (GPT-4o) routes to Status Check
    WF->>CLM: POST /v1/chat/completions (full history + system prompt)

    Note over CLM: Parse [NODE:status_check] + dynamic variables
    CLM->>CLM: Extract citizen_id, first_name from system prompt
    CLM->>CLM: Look up gender from DB for honorific
    CLM->>LG: graph.ainvoke(messages, profile, workflow_node)

    LG->>LG: entry_router -> intent_classify (GPT-4o)
    LG->>LG: intent = "status_check"
    LG->>LG: service_router -> status_check node

    LG->>API: GET /applications?citizen_id=1
    API->>DB: Query applications for citizen 1
    DB-->>API: [passport: in_review, id_card: approved]
    API-->>LG: 2 applications

    Note over LG: Multiple apps -> list both, ask which one

    LG-->>CLM: "Mr. Ahmet, you have 2 applications..."
    CLM->>DB: INSERT conversation_log (intent, timing, node details)
    CLM-->>EL: SSE stream (sentence by sentence)
    EL->>EL: TTS converts text to speech
    EL-->>C: Voice response
```

## Deterministic Tool Chaining

LangGraph's core differentiator — automatic secondary actions based on API results:

```mermaid
graph LR
    SC[Status Check] -->|API call| API[Gov API]
    API -->|status: additional_docs_needed| RAG1[RAG: Required Documents]
    API -->|status: rejected| RAG2[RAG: Appeal Rights]
    API -->|status: approved| DONE1[Return result]
    API -->|status: in_review| DONE2[Return result]
    API -->|status: pending| DONE3[Return result]
    RAG1 --> RESP1[Response + document list]
    RAG2 --> RESP2[Response + appeal guidance]

    style RAG1 fill:#4CAF50,color:#fff
    style RAG2 fill:#4CAF50,color:#fff
```

ElevenLabs native agents can't guarantee this: "if status API returned X, then automatically query knowledge base for Y." This requires programmatic control flow, which is exactly what LangGraph provides.

## ElevenLabs Workflow

The visual routing layer that handles authentication and high-level service selection:

```mermaid
graph TD
    START[Start] --> CI[Collect Identity<br/>GPT-4o-mini + Native KB]
    CI -->|FAQ question| KB[(ElevenLabs<br/>Native Knowledge Base)]
    KB -->|Answer without auth| CI
    CI -->|Credentials collected| DT[Dispatch Tool<br/>POST /auth/verify/webhook]
    DT -->|200 Success| SR[Service Router<br/>GPT-4o]
    DT -->|401 Failure| AR[Auth Retry<br/>GPT-4o-mini]
    AR -->|Retry| CI

    SR -->|Status check| SC[Status Check<br/>Custom LLM / LangGraph]
    SR -->|Appointment| AB[Appointment<br/>Custom LLM / LangGraph]
    SR -->|Document request| DR[Document Request<br/>Custom LLM / LangGraph]
    SR -->|Complaint| CMP[Complaint<br/>Custom LLM / LangGraph]
    SR -->|Operator transfer| OP[Operator Transfer<br/>Phone]
    SR -->|End| END[End Call]

    style CI fill:#2196F3,color:#fff
    style KB fill:#00BCD4,color:#fff
    style DT fill:#FF9800,color:#fff
    style SR fill:#2196F3,color:#fff
    style AR fill:#f44336,color:#fff
    style SC fill:#9C27B0,color:#fff
    style AB fill:#9C27B0,color:#fff
    style DR fill:#9C27B0,color:#fff
    style CMP fill:#9C27B0,color:#fff
```

**Key insight:** Pre-auth FAQ uses ElevenLabs native KB (zero latency, no server round-trip). Post-auth services use Custom LLM / LangGraph (deterministic tool chaining, RAG). Workflow handles deterministic auth routing (yes/no), LangGraph handles intelligent service routing.

## LangGraph Node Graph

```mermaid
graph TD
    START((START)) --> ER[entry_router]
    ER --> IC[intent_classify<br/>GPT-4o]
    IC --> SR[service_router]

    SR -->|status_check| SC[status_check]
    SR -->|appointment_book| AB[appointment_book]
    SR -->|appointment_list| AL[appointment_list]
    SR -->|appointment_cancel| AC[appointment_cancel]
    SR -->|document_request| DR[document_request]
    SR -->|document_status| DS[document_status]
    SR -->|faq / fee_inquiry / unknown| FAQ[faq_answer]
    SR -->|complaint| CMP[complaint]
    SR -->|escalate| ESC[escalate]

    SC --> END((END))
    AB --> END
    AL --> END
    AC --> END
    DR --> END
    DS --> END
    FAQ --> END
    CMP --> END
    ESC --> END
```

## Analytics Pipeline

Every Custom LLM request is logged to `conversation_logs` table:

```mermaid
graph LR
    REQ[Custom LLM Request] --> PARSE[Parse system prompt<br/>Extract NODE, variables]
    PARSE --> GRAPH[LangGraph invoke]
    GRAPH --> LOG[ConversationLog]

    LOG --> |Fields| F1[conversation_id]
    LOG --> F2[intent]
    LOG --> F3[response_time_ms]
    LOG --> F4[intent_classify_ms]
    LOG --> F5[rag_query / rag_score]
    LOG --> F6[service_node_name]
    LOG --> F7[tool_chain_triggered]
    LOG --> F8[api_calls_count]

    LOG --> DASH[Streamlit Dashboard<br/>9 pages]
```

## Database Schema

```mermaid
erDiagram
    Citizen ||--o{ Application : has
    Citizen ||--o{ Appointment : has
    Citizen ||--o{ DocumentRequest : has

    Citizen {
        int id PK
        string tc_kimlik_hash UK
        string tc_last4_hash
        string first_name
        string last_name
        string father_name
        string gender
        string date_of_birth
        string phone_number
        string language_preference
    }

    Application {
        int id PK
        int citizen_id FK
        string application_ref UK
        string service_type
        string status
        string notes
        string office
    }

    Appointment {
        int id PK
        int citizen_id FK
        string service_type
        string appointment_date
        string appointment_time
        string office
        string status
    }

    DocumentRequest {
        int id PK
        int citizen_id FK
        string document_type
        string request_ref UK
        string status
        int estimated_days
    }

    ConversationLog {
        int id PK
        string conversation_id
        string intent
        string workflow_node
        int response_time_ms
        int intent_classify_ms
        string service_node_name
        string tool_chain_triggered
        int api_calls_count
        float rag_score
    }

    AuthAuditLog {
        int id PK
        string session_id
        string method
        string result
        string failure_reason
    }
```
