FROM python:3.10-slim

# libpq-dev/gcc: build psycopg's C extension.
# tesseract-ocr/poppler-utils: the OCR fallback for scanned PDFs
# (rag/loaders.py) shells out to these -- without them, that fallback path
# raises a clear IngestionError instead of silently failing, but scanned
# PDFs just won't work at all until these are present.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies installed before the app code is copied in, so this layer
# only rebuilds when requirements.txt actually changes -- not on every
# code edit.
COPY requirements.txt .
# torch must come from the CPU-only wheel index, installed *before* the
# rest of requirements.txt. Without this, pip resolves plain `torch>=2.2`
# (as listed in requirements.txt) to the default CUDA build -- pulling in
# several hundred MB to multiple GB of nvidia-* packages that a CPU-only
# server will never use, massively slowing the build for no benefit. This
# mirrors how torch was installed during local development (see README).
#
# --extra-index-url (not --index-url): the latter *replaces* PyPI
# entirely rather than adding to it, which breaks the very next step --
# pip can't find ordinary packaging tools like flit_core (needed to build
# typing_extensions from source) on an index that only hosts torch wheels.
RUN pip install --no-cache-dir --default-timeout=180 --retries 5 \
    torch --extra-index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --default-timeout=180 --retries 5 -r requirements.txt

COPY . .
RUN chmod +x docker/entrypoint.sh

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "--workers", "3", "--worker-class", "gthread", "--threads", "4", \
     "--timeout", "120", "--bind", "0.0.0.0:8000", "config.wsgi:application"]
