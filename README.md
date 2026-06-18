# PACR Research Ingestion & Scoring Pipeline

A fully autonomous, stateless Python pipeline that fetches the latest academic papers, evaluates them using an LLM, and pushes the approved ones directly to the PACR Next.js Backend.

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+
- Your PACR Next.js Backend running on `http://localhost:8000` (or configured in `.env`)
- (Optional) A local Ollama server running for LLM scoring

### 2. Configuration
Copy `.env.example` to `.env` and configure your API keys.

```env
# Choose your LLM provider
LLM_PROVIDER=ollama

# PACR Backend Integration (MUST match your Next.js internal key)
PACR_BACKEND_URL=http://localhost:8000
PACR_INTERNAL_API_KEY=your_secret_key_here

# Pipeline schedule
CRON_EXPRESSION=0 0 * * *
PAPERS_PER_SOURCE=5
```

### 3. Launch the Server
Start the FastAPI orchestrator. **No database required.**

```bash
uvicorn app.main:app --reload --port 8001
```

### 4. Trigger the Pipeline
The pipeline will run automatically based on your `CRON_EXPRESSION`. 
You can also manually trigger it anytime via Postman or Curl:

```bash
curl -X POST http://localhost:8001/pipeline/trigger
```

---

## 🧠 How It Works

1. **Fetch**: Pulls the latest papers from OpenAlex, PubMed, and ArXiv.
2. **State Tracking**: Saves the timestamp of the last run to a local `sync_time.json` file to ensure it only fetches new papers on the next run.
3. **Deduplicate**: Asks the Next.js backend if the DOIs already exist.
4. **Enrich & Score**: Uses Semantic Scholar and an LLM to score the papers (0-100).
5. **Publish**: Approved papers (Score ≥ 80) are POSTed to your Next.js backend where they are saved to MongoDB and published.

## 🔑 External Services
- **OpenAlex, PubMed, ArXiv**: Source databases (Free / API keys optional)
- **Semantic Scholar**: Metadata enrichment (Free / API key optional)
- **Ollama / OpenAI / Gemini**: AI Scoring (Local or API key)
- **PACR Backend**: Final destination for approved papers
