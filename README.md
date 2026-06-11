# PACR Research Ingestion & Scoring Pipeline

A **fully autonomous, production-ready** research ingestion engine that fetches papers from five academic sources, evaluates them using an LLM, scores them on a 0–100 scale, and stores only approved papers (score ≥ 80) in MongoDB — all without any human intervention after initial deployment.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     APScheduler (hourly)                     │
└─────────────────────┬───────────────────────────────────────┘
                      │
          ┌───────────▼───────────┐
          │   Pipeline Orchestrator│
          └───────────┬───────────┘
                      │
     ┌────────────────┼────────────────┐
     ▼                ▼                ▼
OpenAlex           PubMed           arXiv
Connector        Connector        Connector
     │                │                │
     └────────────────┼────────────────┘
                      │ NormalizedPaper
                      ▼
             ┌─────────────────┐
             │ Deduplication   │◄── DOI / ExternalID / Title Fuzzy
             └────────┬────────┘
                      │
                      ▼
             ┌─────────────────┐
             │ MongoDB Upsert  │
             └────────┬────────┘
                      │
             ┌────────▼────────┐
             │  Enrichment     │◄── Crossref + Semantic Scholar
             └────────┬────────┘
                      │
             ┌────────▼────────┐
             │  LLM Scoring    │◄── OpenAI / Gemini / OpenRouter
             └────────┬────────┘
                      │
             ┌────────▼────────┐
             │Composite Scoring│  50% LLM + 20% Citation
             │                 │  15% Journal + 15% Author
             └────────┬────────┘
                      │
           ┌──────────┴──────────┐
           ▼                     ▼
    Score ≥ 80              Score < 80
    APPROVED                REJECTED
    Stored in               Stored with
    MongoDB                 rejection reason
           │
           ▼
     FastAPI REST
     (GET /papers)
           │
           ▼
     PACR Website
```

---

## Quick Start

### Prerequisites

- Python 3.10+
- A local MongoDB instance running on your machine (port 27017)
- An API key for at least one LLM provider (or a local Ollama server running)

### 1. Clone and Configure

```bash
git clone <repo-url>
cd pacr-pipeline
cp .env.example .env
```

Edit `.env`:

```env
# Choose your LLM provider (e.g. ollama)
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=llama3

# MongoDB (local)
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=pacr

# Pipeline settings
FETCH_INTERVAL_MINUTES=60
PAPERS_PER_SOURCE=50
APPROVAL_THRESHOLD=80
```

### 2. Launch

Make sure your local MongoDB server is running, then start the application:

```bash
uvicorn app.main:app --reload
```

That's it. The pipeline will:
- Connect to your local MongoDB and create all indexes
- Start the FastAPI server on port 8000
- Run the first ingestion cycle automatically based on your cron schedule

### 3. Verify

```bash
# Check system health
curl http://localhost:8000/health

# Get approved papers
curl http://localhost:8000/papers

# View pipeline stats
curl http://localhost:8000/stats

# Scheduler status
curl http://localhost:8000/scheduler/status
```

---

## API Reference

### Papers

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/papers` | List papers with filtering & pagination |
| GET | `/papers/{id}` | Get a single paper with full score breakdown |
| GET | `/papers/latest` | Most recently ingested approved papers |
| GET | `/papers/top-rated` | Highest scoring approved papers |
| GET | `/papers/search?q=` | Full-text search in titles |

#### Query Parameters for `GET /papers`

| Param | Default | Description |
|-------|---------|-------------|
| `status` | `approved` | `approved`, `rejected`, `pending` |
| `source` | — | `openalex`, `pubmed`, `arxiv` |
| `page` | `1` | Page number |
| `page_size` | `20` | Results per page (max 100) |
| `sort_by` | `scores.final_score` | MongoDB field to sort by |
| `sort_dir` | `-1` | `-1` descending, `1` ascending |
| `search` | — | Full-text search query |

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | System health check |
| GET | `/stats` | Ingestion statistics |
| GET | `/scheduler/status` | Scheduler status and next run |
| POST | `/scheduler/trigger` | Manually trigger a pipeline run |
| GET | `/docs` | Swagger UI |
| GET | `/redoc` | ReDoc UI |

---

## Scoring System

### LLM Evaluation (0–100)

Each paper is scored by the configured LLM on four dimensions (0–25 each):

| Dimension | Description |
|-----------|-------------|
| **Novelty** | Originality and innovation of the research |
| **Credibility** | Author reputation, journal quality signals |
| **Methodology** | Rigor and soundness of the approach |
| **Impact** | Potential significance of the contribution |

### Composite Formula

```
final_score =
  (llm_score      × 0.50) +
  (citation_score × 0.20) +
  (journal_score  × 0.15) +
  (author_score   × 0.15)
```

### Score Normalization

- **Citation score**: Logarithmic scale (log₁₀). 0 citations = 0, 1000 citations ≈ 100
- **Journal score**: Tier-based. Nature/Science = 100, Tier-2 = 65, arXiv = 45, Unknown = 40
- **Author score**: H-index based. H=50 ≈ 100, no data = 20 (neutral)

### Approval

Papers with `final_score >= 80` → **APPROVED** and exposed via API  
Papers with `final_score < 80` → **REJECTED** with stored reason

---

## Data Sources

| Source | Purpose | Rate Limit |
|--------|---------|------------|
| **OpenAlex** | Primary: all domains | 10 req/s (polite pool) |
| **PubMed** | Biomedical papers | 3/s (10/s with API key) |
| **arXiv** | AI, CS, Physics, Math | 1 req/3s |
| **Crossref** | DOI validation & enrichment | 5 req/s |
| **Semantic Scholar** | Citation metrics, h-index | 1/s (higher with key) |

---

## MongoDB Collections

### `papers`
The primary collection. Indexed on: `doi`, `external_id+source`, `status`, `scores.final_score`, `publication_date`, `created_at`

### `sync_state`
Tracks the last successful sync timestamp per source for incremental ingestion.

### `score_logs`
Immutable audit log of every scoring decision.

### `authors`
Author profiles (populated during enrichment).

### `sources`
Source connector metadata.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `openai` | `openai`, `gemini`, or `openrouter` |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `GEMINI_API_KEY` | — | Google Gemini API key |
| `OPENROUTER_API_KEY` | — | OpenRouter API key |
| `LLM_MODEL` | `gpt-4o-mini` | Model name for the chosen provider |
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGODB_DB` | `pacr` | Database name |
| `SEMANTIC_SCHOLAR_API_KEY` | — | Optional, for higher S2 rate limits |
| `NCBI_API_KEY` | — | Optional, for higher PubMed rate limits |
| `FETCH_INTERVAL_MINUTES` | `60` | How often to run the pipeline |
| `PAPERS_PER_SOURCE` | `50` | Max papers fetched per source per run |
| `APPROVAL_THRESHOLD` | `80` | Minimum score to approve a paper |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `json` | `json` (production) or `console` (development) |

---

## Development Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Ensure your local MongoDB server is running!

# Copy and edit environment
cp .env.example .env

# Run the API with auto-reload
uvicorn app.api.main:app --reload --port 8000
```

---

## Running Tests

```bash
# Unit tests only (no MongoDB required)
pytest tests/unit/ -v

# All tests (requires MongoDB)
pytest tests/ -v

# With coverage
pytest tests/unit/ -v --cov=app --cov-report=term-missing
```

---

## Autonomous Operation Details

The system is **fully self-operating** after deployment:

| Capability | Implementation |
|-----------|----------------|
| Auto-fetch | APScheduler triggers every `FETCH_INTERVAL_MINUTES` |
| Incremental sync | `sync_state` collection tracks last successful fetch per source |
| Auto-deduplicate | DOI + ExternalID + Title fuzzy match (RapidFuzz ≥ 92%) |
| Auto-enrich | Crossref + Semantic Scholar on every new paper |
| Auto-score | LLM + composite formula on every new/updated paper |
| Auto-approve/reject | Threshold comparison, status written to MongoDB |
| Auto-retry | Tenacity retry with exponential backoff on all HTTP calls |
| Rate limit recovery | 429 detection → `Retry-After` sleep → automatic retry |
| Overlap prevention | Single-instance job guard prevents concurrent runs |
| Error isolation | Per-paper exception handling; one failure doesn't stop the pipeline |
| Structured logging | Every decision logged with `structlog` in JSON format |

The only actions requiring human involvement are:
1. **Initial deployment** (Run `uvicorn app.main:app`)
2. **Configuration changes** (edit `.env`, restart server)

---

## Production Checklist

- [ ] Set strong `MONGODB_URI` with authentication
- [ ] Use `LOG_FORMAT=json` and ship logs to your logging platform
- [ ] Set `API_DEBUG=false`
- [ ] Configure `NCBI_API_KEY` and `SEMANTIC_SCHOLAR_API_KEY` for higher rate limits
- [ ] Set up MongoDB backups
- [ ] Configure a reverse proxy (nginx/traefik) in front of port 8000
- [ ] Monitor `/health` endpoint with your uptime tool
- [ ] Review `PAPERS_PER_SOURCE` to balance cost and coverage

---

## Project Structure

```
pacr-pipeline/
├── app/
│   ├── api/
│   │   └── main.py              # FastAPI app, all routes
│   ├── connectors/
│   │   ├── base.py              # Abstract base with retry & rate limiting
│   │   ├── openalex.py          # OpenAlex connector
│   │   ├── pubmed.py            # PubMed (eSearch + eFetch)
│   │   ├── arxiv.py             # arXiv Atom feed
│   │   ├── crossref.py          # Crossref enrichment
│   │   └── semantic_scholar.py  # Semantic Scholar enrichment
│   ├── core/
│   │   ├── config.py            # Pydantic settings
│   │   ├── logging.py           # Structured logging
│   │   ├── pipeline.py          # Main orchestrator
│   │   ├── deduplication.py     # Duplicate detection
│   │   └── enrichment.py        # Metadata enrichment
│   ├── db/
│   │   ├── client.py            # Motor client + index creation
│   │   └── repository.py        # All MongoDB operations
│   ├── models/
│   │   └── paper.py             # All Pydantic models
│   ├── scoring/
│   │   ├── llm_scorer.py        # LLM provider abstraction + prompting
│   │   └── composite.py         # Weighted composite scoring
│   └── scheduler/
│       └── scheduler.py         # APScheduler setup
├── tests/
│   ├── unit/                    # No external deps required
│   │   ├── test_scoring.py
│   │   ├── test_models.py
│   │   ├── test_connectors.py
│   │   └── test_llm_scorer.py
│   └── integration/
│       └── test_api.py          # Requires MongoDB
├── requirements.txt
├── pytest.ini
└── .env.example
```

uvicorn app.main:app --reload - to start project
