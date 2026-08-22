"""
Django settings for the siresoft-llm RAG platform.

All environment-dependent and RAG-tunable values are read from `.env` via
django-environ (see .env.example for the full list). Nothing here should be
hardcoded per-deployment secrets or per-request tuning knobs — those belong
in the environment, not in code.
"""

import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

# --------------------------------------------------------------------------
# Core Django
# --------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-only-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "django_celery_results",
    # local apps
    "core",
    "documents",
    "rag",
    "chat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --------------------------------------------------------------------------
# Database — PostgreSQL with the pgvector extension.
# A single Postgres instance serves as both the relational store and the
# vector store, per the project constraints (no separate vector DB).
# --------------------------------------------------------------------------

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://siresoft:siresoft@localhost:5432/siresoft_rag")
}
DATABASES["default"]["ENGINE"] = "django.db.backends.postgresql"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# CORS — only relevant if the frontend is served from a different origin
# (e.g. a Vite dev server during development). Same-origin Django-template
# deployments don't need this open, so it stays opt-in via env.
# --------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# --------------------------------------------------------------------------
# Django REST Framework / OpenAPI schema
# --------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Siresoft RAG API",
    "DESCRIPTION": "Industrial-grade, self-hosted Retrieval-Augmented Generation API.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# --------------------------------------------------------------------------
# Celery — background ingestion pipeline. Redis is broker + result backend.
# Results are additionally persisted via django-celery-results so ingestion
# status survives worker restarts and is queryable from the admin/API.
# --------------------------------------------------------------------------

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = "django-db"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_TRACK_STARTED = True
# A single ingestion task can legitimately run for minutes on a large PDF;
# time limits are generous but not infinite, so a stuck worker can't wedge
# the queue forever.
CELERY_TASK_SOFT_TIME_LIMIT = env.int("CELERY_TASK_SOFT_TIME_LIMIT", default=900)
CELERY_TASK_TIME_LIMIT = env.int("CELERY_TASK_TIME_LIMIT", default=1200)

# --------------------------------------------------------------------------
# Ollama — LLM serving. Models are configured here, not hardcoded in
# templates, so ops can change the fleet without a code deploy.
# --------------------------------------------------------------------------

OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", default="http://localhost:11434")
OLLAMA_MODELS = env.list(
    "OLLAMA_MODELS",
    default=["llama3.1:8b", "qwen2.5:7b", "mistral:7b", "gemma2:9b", "phi3.5:3.8b"],
)
OLLAMA_DEFAULT_MODEL = env("OLLAMA_DEFAULT_MODEL", default=OLLAMA_MODELS[0])
OLLAMA_REQUEST_TIMEOUT = env.int("OLLAMA_REQUEST_TIMEOUT", default=3)
GENERATION_TEMPERATURE = env.float("GENERATION_TEMPERATURE", default=0.1)
# How many prior turns (user+assistant pairs) feed the history-aware query
# rewriter. Kept small on purpose — this prompt runs on every message.
CHAT_HISTORY_TURNS = env.int("CHAT_HISTORY_TURNS", default=5)

# --------------------------------------------------------------------------
# Embeddings — self-hosted via HuggingFace/sentence-transformers.
# Two selectable profiles: a strong default and a lightweight fallback for
# low-RAM servers. The active profile name is stamped onto every Document
# because switching profiles invalidates existing embeddings.
# --------------------------------------------------------------------------

EMBEDDING_PROFILES = {
    "bge-base": {
        "model_name": "BAAI/bge-base-en-v1.5",
        "dimensions": 768,
        # BGE models are trained with an asymmetric instruction prefix:
        # queries need it, passages don't. Getting this backwards quietly
        # tanks retrieval quality without ever raising an error.
        "query_instruction": "Represent this sentence for searching relevant passages: ",
    },
    "minilm": {
        "model_name": "sentence-transformers/all-MiniLM-L6-v2",
        "dimensions": 384,
        "query_instruction": "",
    },
}
EMBEDDING_PROFILE = env("EMBEDDING_PROFILE", default="bge-base")
if EMBEDDING_PROFILE not in EMBEDDING_PROFILES:
    raise ValueError(
        f"EMBEDDING_PROFILE={EMBEDDING_PROFILE!r} is not defined in EMBEDDING_PROFILES"
    )
EMBEDDING_DIMENSIONS = EMBEDDING_PROFILES[EMBEDDING_PROFILE]["dimensions"]

RERANKER_MODEL = env("RERANKER_MODEL", default="BAAI/bge-reranker-base")
EMBEDDING_BATCH_SIZE = env.int("EMBEDDING_BATCH_SIZE", default=32)

# --------------------------------------------------------------------------
# Chunking — configurable, not buried inside a function.
# --------------------------------------------------------------------------

CHUNK_SIZE = env.int("CHUNK_SIZE", default=800)
CHUNK_OVERLAP = env.int("CHUNK_OVERLAP", default=120)

# --------------------------------------------------------------------------
# Ingestion — OCR fallback trigger for scanned/image PDFs.
# If PyMuPDF extracts fewer than this many characters per page on average,
# the page is assumed to be an image and re-processed through Tesseract.
# --------------------------------------------------------------------------

OCR_MIN_CHARS_PER_PAGE = env.int("OCR_MIN_CHARS_PER_PAGE", default=20)

# --------------------------------------------------------------------------
# Retrieval — hybrid search + RRF fusion + cross-encoder rerank tuning.
# --------------------------------------------------------------------------

RETRIEVAL_TOP_K = env.int("RETRIEVAL_TOP_K", default=5)
RETRIEVAL_CANDIDATE_POOL = env.int("RETRIEVAL_CANDIDATE_POOL", default=20)
# The reranker's calibrated Sigmoid probability sits well under 0.01 for
# genuinely irrelevant content and 0.7+ for confident real matches, for the
# default RERANKER_MODEL (bge-reranker-base) -- empirically confirmed via
# `manage.py prove_retrieval`. Borderline-but-real matches (e.g. a
# full-sentence question the reranker under-scores) can sit as low as
# ~0.02, so this is intentionally conservative rather than tuned tight
# against the ~0.99 confident-match ceiling.
#
# IMPORTANT: this default is paired specifically with bge-reranker-base's
# score distribution. Swapping RERANKER_MODEL (e.g. to bge-reranker-v2-m3,
# which scores on a much smaller absolute scale -- irrelevant ~0.00002,
# real matches from ~0.0002 up to ~0.98) requires recalibrating this value
# too, or the threshold will silently reject everything (too high) or
# accept everything (too low). Don't assume one threshold works across
# reranker models.
RETRIEVAL_SIMILARITY_THRESHOLD = env.float("RETRIEVAL_SIMILARITY_THRESHOLD", default=0.01)
# RRF's k constant dampens the influence of low ranks; 60 is the standard
# value from the original Reciprocal Rank Fusion paper (Cormack et al.).
RETRIEVAL_RRF_K = env.int("RETRIEVAL_RRF_K", default=60)

# --------------------------------------------------------------------------
# Uploads
# --------------------------------------------------------------------------

MAX_UPLOAD_SIZE_MB = env.int("MAX_UPLOAD_SIZE_MB", default=100)
ALLOWED_UPLOAD_EXTENSIONS = env.list(
    "ALLOWED_UPLOAD_EXTENSIONS", default=[".pdf", ".txt", ".md", ".docx"]
)

# --------------------------------------------------------------------------
# Logging — every retrieval/generation call is logged with question,
# retrieved chunk IDs, model used, and latency (see rag.logging).
# --------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "documents": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "rag": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "chat": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
