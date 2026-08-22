# Deployment: Gunicorn + systemd + nginx

Assumes a Linux server (Ubuntu/Debian-style paths below), the app checked
out at `/opt/siresoft-rag`, running as a dedicated `siresoft` system user,
with PostgreSQL+pgvector, Redis, and Ollama already set up per the
[README](README.md) (either on this host or reachable over the network).

## 0. One architectural thing to get right: Gunicorn's worker class

The chat endpoint (`POST /api/chat/sessions/{id}/messages/`) is a
long-lived `StreamingHttpResponse` — the generator blocks on Ollama between
tokens for the entire duration of an answer (seconds to tens of seconds).
Under Gunicorn's **default `sync` worker class, one worker = one request at
a time for its entire duration**, so a handful of concurrent chats would
starve every other request on the server, streaming or not.

Use `gthread` (threaded sync workers) instead, so each worker process can
hold several requests in flight at once:

```
--worker-class gthread --workers 3 --threads 4
```

This isn't a hard requirement of Django/DRF — it's specific to this app
having a genuinely long-lived streaming endpoint in the mix.

## 1. System user and directories

```bash
sudo useradd --system --create-home --shell /bin/bash siresoft
sudo mkdir -p /opt/siresoft-rag
sudo chown siresoft:siresoft /opt/siresoft-rag
```

Deploy the code to `/opt/siresoft-rag` (git clone or rsync), as the
`siresoft` user, then:

```bash
sudo -u siresoft -H bash -c '
  cd /opt/siresoft-rag
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  cp .env.example .env   # then edit .env for real
'
```

Edit `/opt/siresoft-rag/.env`:
- `DJANGO_DEBUG=False`
- `DJANGO_SECRET_KEY=` a real random value (`python -c "import secrets; print(secrets.token_urlsafe(50))"`)
- `DJANGO_ALLOWED_HOSTS=` your actual domain(s)
- `DATABASE_URL`, `REDIS_URL`, `OLLAMA_BASE_URL` pointing at your real services

```bash
sudo -u siresoft -H bash -c '
  cd /opt/siresoft-rag
  .venv/bin/python manage.py migrate
  .venv/bin/python manage.py collectstatic --noinput
  .venv/bin/python manage.py createsuperuser
'
```

## 2. Gunicorn systemd unit

`/etc/systemd/system/siresoft-gunicorn.service`:

```ini
[Unit]
Description=Siresoft RAG - Gunicorn
After=network.target postgresql.service redis-server.service

[Service]
User=siresoft
Group=siresoft
WorkingDirectory=/opt/siresoft-rag
EnvironmentFile=/opt/siresoft-rag/.env
ExecStart=/opt/siresoft-rag/.venv/bin/gunicorn \
    --workers 3 \
    --worker-class gthread \
    --threads 4 \
    --timeout 120 \
    --bind unix:/run/siresoft-rag/gunicorn.sock \
    config.wsgi:application
RuntimeDirectory=siresoft-rag
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`--timeout 120` (Gunicorn's *worker* timeout, not to be confused with
nginx's read timeout below) gives a slow generation stream enough room
before Gunicorn considers the worker hung and recycles it.

## 3. Celery worker systemd unit

`/etc/systemd/system/siresoft-celery.service`:

```ini
[Unit]
Description=Siresoft RAG - Celery worker
After=network.target redis-server.service postgresql.service

[Service]
User=siresoft
Group=siresoft
WorkingDirectory=/opt/siresoft-rag
EnvironmentFile=/opt/siresoft-rag/.env
ExecStart=/opt/siresoft-rag/.venv/bin/celery -A config worker \
    --loglevel=info \
    --concurrency=2
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`--concurrency=2` is deliberately modest: each ingestion task loads
already-cached embedding/reranker models but still does real CPU-bound
work (chunking, embedding, OCR if triggered) — size this to your server's
actual core count, not up reflexively. Prefork workers each hold their own
copy of the embedding + reranker models in memory (~2GB combined by
default), so concurrency also has a real RAM cost, not just a CPU one.

Enable both:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now siresoft-gunicorn siresoft-celery
```

## 4. nginx

The one thing that will silently break the chat feature if you copy a
generic Django nginx config verbatim: **response buffering must be off**
for the streaming endpoint, or nginx will wait for the entire SSE response
to finish before forwarding any of it to the browser — the user sees
nothing, then the whole answer appears at once, defeating the point of
streaming.

`/etc/nginx/sites-available/siresoft-rag`:

```nginx
upstream siresoft_rag {
    server unix:/run/siresoft-rag/gunicorn.sock;
}

server {
    listen 80;
    server_name your-domain.example.com;

    client_max_body_size 110M;  # match MAX_UPLOAD_SIZE_MB + a small margin

    location /static/ {
        alias /opt/siresoft-rag/staticfiles/;
        expires 30d;
    }

    location /media/ {
        alias /opt/siresoft-rag/media/;
    }

    # Streaming chat endpoint: buffering off, generous read timeout.
    location ~ ^/api/chat/sessions/\d+/messages/$ {
        proxy_pass http://siresoft_rag;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;
        # A slow model on a loaded server can legitimately take a while;
        # this is the ceiling before nginx gives up on the upstream.
        proxy_read_timeout 300s;
    }

    location / {
        proxy_pass http://siresoft_rag;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/siresoft-rag /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Put this behind TLS (`certbot --nginx`) before exposing it beyond a
private network — none of the above adds HTTPS on its own.

## 5. Security checklist before going live

- [ ] `DJANGO_DEBUG=False`
- [ ] `DJANGO_SECRET_KEY` is a real random value, not the dev default, and isn't committed anywhere
- [ ] `DJANGO_ALLOWED_HOSTS` is your actual domain(s), not `*`
- [ ] Postgres, Redis, and Ollama are bound to `localhost` / a private network — not reachable from the public internet directly (only nginx should be)
- [ ] `.env` is readable only by the `siresoft` user (`chmod 600 .env`)
- [ ] TLS is configured (certbot or equivalent) and HTTP redirects to HTTPS
- [ ] `MAX_UPLOAD_SIZE_MB` in `.env` matches `client_max_body_size` in nginx

## 6. Operating it

```bash
# Logs
sudo journalctl -u siresoft-gunicorn -f
sudo journalctl -u siresoft-celery -f

# Deploying a code change
cd /opt/siresoft-rag
sudo -u siresoft git pull
sudo -u siresoft .venv/bin/pip install -r requirements.txt
sudo -u siresoft .venv/bin/python manage.py migrate
sudo -u siresoft .venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart siresoft-gunicorn siresoft-celery
```

Pulling a new Ollama model doesn't need a restart — `GET /api/chat/models/`
re-checks `/api/tags` on a 30-second cache, so it appears in the dropdown
shortly after `ollama pull <model>` completes.
