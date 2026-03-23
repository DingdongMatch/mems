# Mems Layered Memory System - Technical Documentation

## 1. Project Overview

**Mems** is a multi-agent isolated, long-term (100-year), vendor-agnostic layered memory system. It transforms fragmented conversations into persistent structured knowledge through "cold-hot separation" and "asynchronous distillation" technologies.

### Core Features

- **Full Automation Pipeline**: L0→L1→L2→L3 completely automated
- **Four-layer Memory Architecture**: L0(Redis) → L1(SQL+Vector) → L2(SQL+JSONL) → L3(JSONL Archive)
- **Multi-tenant Isolation**: Each Agent has independent storage with physical isolation
- **Vector Search**: Semantic search based on Qdrant
- **Memory Distillation**: L1→L2 automatic filter, extract, reconcile, and commit pipeline
- **Centennial Archive**: Pure text JSONL format, readable across eras

---

## 2. Tech Stack

| Component | Technology | Description |
|-----------|------------|-------------|
| Language | Python 3.12+ | Modern async support |
| Web Framework | FastAPI | High-performance, easy to use |
| ORM | SQLModel | Pydantic + SQLAlchemy |
| Vector DB | Qdrant | Efficient vector search |
| Cache | Redis | L0 Working Memory |
| Scheduler | APScheduler | Cron jobs |
| Database | SQLite (Dev) / PostgreSQL (Prod) | Relational storage |
| Package Manager | uv | Modern Python package manager |

---

## 3. Embedding Model

### 3.1 Role Description

The Embedding model is the **core component of the L1 layer (episodic memory)** for enabling semantic search.

```
User Query → Embedding Model → 512-dim Vector → Qdrant Vector Search → Relevant Memory
```

#### Primary Uses

1. **During Search** (search endpoint)
   - Convert user query to vector → Perform similarity search in Qdrant → Return most relevant L1 memories

2. **During Write** (l0_sync)
   - When L0 data syncs to L1, generate vectors and store in Qdrant

#### Data Flow Example

```
Search Request: "What does the user like?"
     ↓
Embedding Model (bge-small-zh-v1.5) → [0.12, -0.34, 0.56, ...]
     ↓
Qdrant Vector Search (Cosine Similarity) → Find most similar L1 records
     ↓
Return Results (source: l1_episodic, score: 0.95)
```

### 3.2 Model Configuration

| Config | Description | Default |
|--------|-------------|---------|
| `EMBEDDING_PROVIDER` | Provider (sentence-transformers/openai) | sentence-transformers |
| `SENTENCE_TRANSFORMERS_MODEL` | Local model name | BAAI/bge-small-zh-v1.5 |
| `OPENAI_EMBEDDING_MODEL` | OpenAI model | text-embedding-3-small |

#### Local Model (Default)

- **Model**: `BAAI/bge-small-zh-v1.5`
- **Vector Dimension**: 512
- **Features**: Chinese-optimized, lightweight, no API calls needed

#### OpenAI (Optional)

To use OpenAI embedding, configure in `.env`:

```bash
EMBEDDING_PROVIDER=openai
OPENAI_EMBEDDING_API_KEY=your-key
```

---

## 4. Layer Definition

The current codebase is a runnable reference implementation. For system design and future production evolution, the four layers can be defined as follows:

| Layer | Logical Definition |
|-------|--------------------|
| L0: Instant Layer | Ongoing conversation, current task state, and chain-of-thought in progress |
| L1: Episodic Layer | Raw conversation records and recent event details |
| L2: Semantic Layer | Distilled user profile, factual knowledge, behavioral preferences, and rolling summaries |
| L3: Archive Layer | Full historical logs, annual summaries, and inactive knowledge |

---

## 5. Architecture Design

### 5.1 Four-Layer Memory Model

```
┌─────────────────────────────────────────────────────────────────┐
│                    L0: Working Memory (Redis)                  │
│              Auto sync to L1, seamless for users                │
│  - Current session context                                      │
│  - Active task state                                            │
│  - Temporary variables                                          │
│  - TTL auto expiration                                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ (Real-time auto sync)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              L1: Episodic Memory (SQLite + Qdrant + JSONL)      │
│  - Raw conversation 100% preserved                             │
│  - Vector embeddings for semantic search                         │
│  - Text-first: sync write to JSONL                             │
└─────────────────────────────────────────────────────────────────┘
                              │                        │
                              │ (Threshold/Cron)      │ (Cron)
                              ▼                        ▼
┌─────────────────────────────────────────────────────────────────┐
│              L2: Semantic Memory (SQLite + JSONL)               │
│  - Profile items / facts / events / summaries                   │
│  - Conflict detection + versioning + evidence lineage           │
│  - Traceable to L1 records                                     │
│  - Trigger: >100 undisted OR daily 2:00 AM                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ (Cron)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    L3: Archive Memory (JSONL Files)              │
│  - Data older than 30 days                                      │
│  - Pure text format, readable for 100 years                   │
│  - Self-contained reader.py (stdlib only)                       │
│  - Trigger: daily 3:00 AM                                      │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 Automated Data Flow

```
User ──→ /memories/write ──→ L0 (Redis)
                                 │
                                 │ (Auto persist)
                                 ▼
                           L1 (SQL + Qdrant + JSONL)
                          │
      ┌───────────────────┴───────────────────┐
      │                                       │
      │ (Threshold: >100 undisted)          │ (Daily: 3:00 AM)
      ▼                                       ▼
  L2 (Profile/Facts/Summaries)             L3 (Archive)
```

---

## 6. Automation Pipeline Details

### 6.1 L0 → L1 Auto Dual Write

When user calls `/memories/write`, system automatically:

1. Write to Redis (L0) - with TTL
2. Auto sync to L1 - SQLite + Qdrant + JSONL

**Config**:
- `L0_AUTO_SYNC_L1=true` - Enable auto sync
- `L0_DEFAULT_TTL_SECONDS=1800` - L0 default TTL

### 6.2 L1 → L2 Auto Distillation

**Trigger Conditions** (any one triggers):
- **Threshold**: Undistilled L1 records > 100 (configurable)
- **Scheduled**: Daily at 2:00 AM (configurable)

**Processing**:
1. Query `is_distilled=False` L1 records
2. Filter greetings, low-signal fragments, and ephemeral tasks
3. Extract profile updates, facts, events, and summary candidates
4. Reconcile them against existing L2 context
5. Commit profile/fact/event/summary/conflict records and update `L1.is_distilled=True`

### 6.3 L1 → L3 Auto Archive

**Trigger Condition**:
- **Scheduled**: Daily at 3:00 AM (configurable)

**Processing**:
1. Find L1 records older than 30 days (configurable)
2. Export to JSONL file
3. Create L3 index record
4. Mark the original L1 records as archived while keeping them available for online retrieval

### 6.4 Scheduler Configuration

| Config | Default | Description |
|--------|---------|-------------|
| `SCHEDULER_ENABLED` | true | Enable scheduler |
| `DISTILL_CRON_HOUR` | 2 | Distill hour |
| `DISTILL_CRON_MINUTE` | 0 | Distill minute |
| `DISTILL_THRESHOLD` | 100 | Distill threshold |
| `DISTILL_BATCH_SIZE` | 50 | Distill batch size |
| `ARCHIVE_CRON_HOUR` | 3 | Archive hour |
| `ARCHIVE_CRON_MINUTE` | 0 | Archive minute |
| `ARCHIVE_DAYS` | 30 | Archive after days |

---

## 7. API Endpoints

### 7.1 Memory Write

**POST** `/memories/write` - Write memory and let the system auto-persist to L0/L1
```json
{
  "agent_id": "agent_001",
  "session_id": "sess_abc",
  "messages": [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi!"}
  ],
  "active_plan": "learning",
  "temp_variables": {"key": "value"},
  "ttl_seconds": 1800
}
```

### 7.2 Memory Search

**POST** `/memories/search`
```json
{
  "agent_id": "agent_001",
  "query": "programming language learning",
  "top_k": 5
}
```

The retrieval strategy is internal-only: the API does not expose L1/L2 toggles.

Internally, search uses query-intent routing and freshness-aware ranking across episodic, profile, fact, event, and summary memories.

Long-term summaries are also vector-indexed in the shared agent collection, and monitor status now exposes stale profile/fact/summary counts derived from `last_verified_at`.

### 7.3 Monitor Status

**GET** `/monitor/status` - View dependency health, scheduler state, and pipeline backlog

### 7.4 Lightweight Health Probe

**GET** `/health` - Lightweight process liveness endpoint

---

## 8. Configuration

### Development Reset

```bash
uv run python scripts/reset_dev_state.py
```

### Environment Variables (.env)

```bash
# Database
DATABASE_URL=sqlite:///mems.db

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# Qdrant
QDRANT_HOST=localhost
QDRANT_PORT=6333

# Embedding Model (local sentence-transformers)
EMBEDDING_PROVIDER=sentence-transformers
SENTENCE_TRANSFORMERS_MODEL=BAAI/bge-small-zh-v1.5

# LLM Distillation (OpenAI/DashScope)
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen3.5-plus

# Scheduler (Automation Pipeline)
SCHEDULER_ENABLED=true
DISTILL_CRON_HOUR=2
DISTILL_CRON_MINUTE=0
DISTILL_THRESHOLD=100
DISTILL_BATCH_SIZE=50
ARCHIVE_CRON_HOUR=3
ARCHIVE_CRON_MINUTE=0
ARCHIVE_DAYS=30

# L0 Auto Sync
L0_AUTO_SYNC_L1=true
L0_DEFAULT_TTL_SECONDS=1800
```

---

## 9. Quick Start

### 9.1 Start Services

```bash
# 1. Install dependencies
uv sync

# 2. Start Docker services (Redis + Qdrant)
docker compose up -d

# 3. Initialize database
python scripts/init_db.py

# 4. Start FastAPI
uv run python -m mems.main
```

### 9.2 Reset Development State

```bash
uv run python scripts/reset_dev_state.py
```

### 9.3 Recommended Usage

```bash
# 1. Write memory
curl -X POST http://localhost:8000/memories/write \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "my_agent",
    "session_id": "sess_001",
    "messages": [{"role": "user", "content": "I like Python"}]
  }'

# 2. Search (internally mixes L1 + L2)
curl -X POST http://localhost:8000/memories/search \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "my_agent", "query": "Python"}'

# 3. Monitor service health and backlog
curl http://localhost:8000/monitor/status
```

---

## 10. Extension Guide

### 10.1 Switch Embedding Model

```bash
EMBEDDING_PROVIDER=openai
OPENAI_EMBEDDING_API_KEY=sk-xxx
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

### 10.2 Switch LLM for Distillation

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4o-mini
```

### 10.3 Switch Database

```bash
DATABASE_URL=postgresql://user:pass@localhost/mems
```

---

## 11. Directory Structure

```
mems/
├── pyproject.toml              # Project config
├── docker-compose.yml          # Docker services
├── .env                       # Environment variables
├── README.md                  # Project readme
├── src/mems/
│   ├── __init__.py
│   ├── main.py                # FastAPI entry + Scheduler
│   ├── config.py              # Configuration
│   ├── models.py             # SQLModel definitions
│   ├── database.py            # Database connection
│   ├── schemas.py             # Pydantic models
│   ├── services/
│   │   ├── scheduler.py       # Scheduler (APScheduler)
│   │   ├── redis_service.py   # L0 service
│   │   ├── l0_sync.py        # L0→L1 sync
│   │   ├── vector_service.py  # Qdrant service
│   │   ├── embedding.py     # Embedding service
│   │   ├── distill.py        # Distill service + threshold
│   │   ├── archive.py        # Archive service + auto trigger
│   │   └── jsonl_utils.py    # JSONL utilities
│   └── routers/
│       ├── memories.py        # /memories/write + /memories/search
│       └── monitor.py         # /monitor/status
├── scripts/
│   ├── init_db.py           # Database init
│   └── reader.py            # L3 reader (stdlib only)
└── storage/
    ├── l1_raw/              # L1 JSONL
    ├── l2_knowledge/        # L2 JSONL
    └── l3_archive/          # L3 archive
```

---

## 12. Notes

1. **Automation First**: Use `/memories/write`, system auto-persists through the pipeline
2. **Text-first**: All data must be written to JSONL simultaneously
3. **Agent Isolation**: Each Agent uses independent Collections
4. **Version Management**: When L2 conflicts, old version inactive, new version +1
5. **LLM Config**: Distillation requires OpenAI/DashScope API
6. **Freshness**: `last_verified_at` participates in ranking and stale-memory monitoring

---

## 13. Progress and Roadmap

### Current Progress

- Public API has been reduced to `POST /memories/write`, `POST /memories/search`, and `GET /monitor/status`
- Distillation now uses a typed extraction contract and stores profile/fact/event/summary/conflict records
- Archive keeps L1 online while exporting cold storage to L3
- Search uses intent-aware routing, freshness-aware ranking, and summary vector recall
- Automated regression tests cover write/search/archive/distill/reconciliation flows

### Next Steps

- Add automatic re-verification jobs for stale semantic memory
- Add vector indexing for profile and fact memories
- Improve contradiction taxonomy and confidence merging in reconciliation
- Generate weekly and monthly rolling summaries
- Add evaluation tooling for retrieval and distillation quality

---

## 14. FAQ

**Q: What config needed for L0 auto sync?**
A: Just ensure `L0_AUTO_SYNC_L1=true`

**Q: Distillation not auto triggering?**
A: Check: 1) Threshold reached (default 100)? 2) Scheduler enabled? 3) LLM available?

**Q: Archive triggers on what?**
A: Time-based (daily 3:00 AM), auto archive data older than 30 days

**Q: Can I trigger manually?**
A: No public manual trigger endpoints are exposed; distill and archive run automatically in the background

---

*Document Version: 0.2.0*
*Last Updated: 2026-03-21*
