# Siresoft RAG

A self-hosted Retrieval-Augmented Generation system: upload PDFs, DOCX,
text/Markdown files, or web pages; ask questions in a chat UI; get answers
grounded strictly in what you uploaded, with citations back to the source
document and page/section. Every model in the stack — LLM, embeddings,
reranker — runs locally. Nothing calls out to a paid API.

**Stack:** Django + Django REST Framework · LangChain (LCEL) · PostgreSQL +
pgvector · Celery + Redis · Ollama · HuggingFace/sentence-transformers ·
Tailwind CSS + Alpine.js.

## Architecture

Four Django apps, each with one job:

| App | Responsibility |
|---|---|
| `core` | Shared abstract models, exceptions, the Ollama-availability helper, the app-shell view |
| `documents` | Upload, validation, SHA-256 dedup, the Celery ingestion pipeline |
| `rag` | Loaders, chunking, embeddings, hybrid retrieval, reranking, the LangChain generation chain |
| `chat` | Sessions, messages, the SSE streaming endpoint |

Ingestion (`documents/tasks.py`) runs in a Celery worker: `load_document` →
`split_document` → embed → `persist_chunks`, with the `Document.status`
field tracking `queued → parsing → chunking → embedding → indexed`
(or `failed`, with a human-readable `error_message`).

Retrieval (`rag/retrieval.py`) is hybrid: pgvector cosine search (HNSW
index) + Postgres full-text search (generated `tsvector` column + GIN
index), fused with Reciprocal Rank Fusion, then reranked with a
cross-encoder (`BAAI/bge-reranker-base`). Run `python manage.py
prove_retrieval` to see this measured against an adversarial test corpus.

Generation (`rag/generation.py`, orchestrated by `chat/services.py`)
rewrites follow-up questions into standalone queries using chat history,
retrieves, and streams a grounded answer back over Server-Sent Events —
short-circuiting with "no relevant content found" if nothing clears
`RETRIEVAL_SIMILARITY_THRESHOLD`, rather than calling the LLM on empty
context.

## Prerequisites

- Python 3.10+
- PostgreSQL 16+ with the `pgvector` extension
- Redis (Celery broker)
- Ollama, with at least one model pulled
- ~3GB disk for the embedding + reranker models (downloaded automatically
  on first use, then cached under `~/.cache/huggingface`)

## 1. PostgreSQL + pgvector

**Option A — Docker (fastest for local dev):**

```bash
docker run -d --name siresoft-pg \
  -e POSTGRES_USER=siresoft -e POSTGRES_PASSWORD=siresoft -e POSTGRES_DB=siresoft_rag \
  -p 5432:5432 pgvector/pgvector:pg16
```

**Option B — native install (Ubuntu/Debian):**

```bash
sudo apt update
sudo apt install -y postgresql-16 postgresql-16-pgvector redis-server

sudo -u postgres psql -c "CREATE USER siresoft WITH PASSWORD 'siresoft';"
sudo -u postgres psql -c "CREATE DATABASE siresoft_rag OWNER siresoft;"
```

The `vector` extension itself is enabled automatically by this project's
first migration (`rag/migrations/0001_initial.py`, via `pgvector.django.VectorExtension`)
— you don't need to run `CREATE EXTENSION` by hand, just make sure the
`pgvector` *package* is installed on the Postgres server so the extension
is available to enable.

## 2. Redis

```bash
# Docker
docker run -d --name siresoft-redis -p 6379:6379 redis:7-alpine

# or, if you installed postgresql-16 above on Debian/Ubuntu, redis-server
# is already installed and running as a system service.
```

## 3. Ollama + models

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &   # if it isn't already running as a service

# Pull whichever of the 5 configured models you want available in the
# dropdown -- you don't need all of them. The UI disables (with a
# tooltip) any model that isn't actually pulled.
ollama pull llama3.1:8b     # strong general-purpose default
ollama pull qwen2.5:7b      # strong reasoning, fully open license
ollama pull mistral:7b      # fast, low resource usage
ollama pull gemma2:9b       # strong instruction-following
ollama pull phi3.5:3.8b     # lightweight fallback for a loaded server
```

## 4. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

OCR fallback for scanned PDFs additionally needs two system binaries
(only required if you expect to ingest scanned/image-only PDFs):

```bash
sudo apt install -y tesseract-ocr poppler-utils
```

## 5. Configure

```bash
cp .env.example .env
```

Edit `.env` — at minimum set `DJANGO_SECRET_KEY` and `DATABASE_URL` to
match what you set up in step 1. Every tunable (chunk size, retrieval
thresholds, upload limits, the model roster, ...) is documented inline in
`.env.example` — nothing is hardcoded in the middle of application code.

## 6. Migrate and create an admin user

```bash
python manage.py migrate
python manage.py createsuperuser
```

The admin site (`/admin/`) lets an operator inspect and clean up
Documents, Chunks, ChatSessions, and ChatMessages, including a bulk
"re-run ingestion" action on the Document list.

## 7. Build the frontend CSS

Tailwind is compiled via its standalone CLI — no Node.js/npm required.
The compiled `static/css/app.css` is already committed, so this step is
only needed if you change `static_src/input.css` or add new Tailwind
classes to a template:

```bash
./tools/get-tailwind.sh    # downloads tools/tailwindcss (~110MB, gitignored)
./tools/tailwindcss -i static_src/input.css -o static/css/app.css --minify
```

## 8. Run it

Three processes, in separate terminals (all need the same `.env`):

```bash
# Celery worker -- runs the ingestion pipeline in the background
celery -A config worker --loglevel=info

# Django dev server
python manage.py runserver
```

Open `http://localhost:8000/`. The REST API is documented at
`/api/docs/` (Swagger UI) and `/api/schema/` (raw OpenAPI).

## Running the tests

```bash
python manage.py test
```

Needs a live Postgres+pgvector connection (Django creates and tears down
a throwaway test database automatically) — everything else, including the
embedding model, reranker, and LLM, is mocked at those boundaries, so the
suite runs in under a second and needs no GPU, no downloaded models, and
no running Ollama server. Covers chunking (`rag/tests/test_splitting.py`),
retrieval scoring (`rag/tests/test_retrieval.py`, including the RRF fusion
math against a live pgvector/full-text DB), the ingestion pipeline
(`documents/tests/test_tasks.py`, `documents/tests/test_validators.py`,
`rag/tests/test_loaders.py`), and chat turn orchestration (`chat/tests.py`).

## Verifying retrieval quality

```bash
python manage.py prove_retrieval
```

Indexes a small adversarial corpus (exact error codes, a lexically-similar
distractor document, a control document) and reports dense-only vs.
hybrid-RRF vs. hybrid-RRF-plus-rerank side by side for each test query —
this is a real, live-model demonstration of why the reranking step exists,
not a static fixture.

## Configuration reference

Every setting is in `.env.example` with an explanation of what it does and
why its default is what it is. The ones most worth knowing about:

- `EMBEDDING_PROFILE` — switching this **invalidates the existing index**
  (different model, different vector space); re-run ingestion on all
  documents after changing it. `Document.embedding_model_name` records
  which model produced each document's vectors, precisely so this kind of
  drift is detectable.
- `RETRIEVAL_SIMILARITY_THRESHOLD` — tuned against the *reranker's*
  sigmoid-normalized score, not raw cosine similarity. Don't set it below
  ~0.5 (see the comment in `.env.example` for why).
- `CHUNK_SIZE` / `CHUNK_OVERLAP` — token-based (via tiktoken), not
  character-based.

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for Gunicorn + systemd + nginx
production deployment, including the Celery worker unit and the nginx
settings SSE streaming actually needs (buffering *must* be off on that
endpoint or the browser will see nothing until the response completes).
