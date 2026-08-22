# Docker Deployment (VM / server)

This deploys the whole stack — Postgres+pgvector, Redis, Django (Gunicorn),
Celery worker, nginx — as Docker containers, with **Ollama running natively
on the VM** (not containerized — see "Why Ollama stays outside Docker"
below).

Everything here runs **detached** (`-d`) with `restart: unless-stopped`.
That combination means: closing your SSH session does **not** stop the
project, and if the VM itself reboots, everything comes back up
automatically once Docker's own service starts (which is enabled by
default on any standard Docker install). You don't need `screen`, `tmux`,
or `nohup` for any of this — that's what `-d` + restart policies are for.

## 0. Why Ollama stays outside Docker

Two reasons, both real:

1. **Performance.** LLM inference is CPU/GPU-heavy; running it natively
   avoids any container virtualization overhead, and if you ever add a
   GPU, passthrough is trivial on bare metal but needs the
   `nvidia-container-toolkit` (extra setup, occasional version mismatches)
   inside Docker.
2. **It already has its own reliable "keep running" mechanism** — a
   systemd service (set up in step 3) — so containerizing it wouldn't add
   any reliability Docker gives you that systemd doesn't already provide.

Everything else (Django, Celery, Postgres, Redis, nginx) has no such
concern, and Docker Compose is the more reliable, more reproducible way to
run all of it together — see step 4.

## 1. VM prerequisites

A fresh Ubuntu/Debian VM, with a non-root sudo user, and at least:
- 4 CPU cores, 8GB RAM (more if you'll run larger Ollama models)
- 30GB+ disk (embedding + reranker models + Ollama models add up)

Open port 80 (and 443 once you add TLS) in your cloud provider's
firewall/security group, in addition to SSH (22).

## 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker   # or log out and back in, so `docker` works without sudo
```

Verify:
```bash
docker compose version
```

Docker's own service is enabled by default on install — confirm with:
```bash
systemctl is-enabled docker   # should print "enabled"
```

## 3. Install Ollama natively + make it survive reboots/disconnects

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

The official installer sets up Ollama as a **system-level** systemd
service automatically (`ollama.service`, running as its own `ollama`
user) — unlike a manual/portable install, you don't need to write the
unit file yourself. Confirm it's enabled and running:

```bash
sudo systemctl enable --now ollama
systemctl status ollama
```

Pull whichever models you want available in the dropdown (you don't need
all 5 — the UI disables any that aren't pulled):

```bash
ollama pull llama3.1:8b
ollama pull qwen2.5:7b
# ...etc, per README.md's model list
```

## 4. Get the project onto the VM

```bash
git clone <your-repo-url> siresoft-rag
cd siresoft-rag
cp .env.example .env
```

Edit `.env`:
- `DJANGO_SECRET_KEY` — a real random value: `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS` — your VM's domain or IP
- `OLLAMA_MODELS` — match whatever you actually pulled in step 3

You can leave `DATABASE_URL`, `REDIS_URL`, and `OLLAMA_BASE_URL` as-is in
`.env` — `docker-compose.yml` overrides all three automatically to the
correct in-network values (see the `environment:` blocks in
`docker-compose.yml` if you want to see exactly what's overridden and why).

Optionally add these to `.env` to change the Postgres container's
credentials from the defaults (`siresoft`/`siresoft`/`siresoft_rag`):
```
POSTGRES_USER=siresoft
POSTGRES_PASSWORD=<something-real-for-production>
POSTGRES_DB=siresoft_rag
```

## 5. Build and start everything

```bash
docker compose up -d --build
```

This builds the app image (Django + Celery share one image, per the
`Dockerfile`), then starts `db` and `redis` first, waits for their
healthchecks to pass, then starts `web`, `worker`, and `nginx`. Migrations
and static file collection run automatically on every container start
(`docker/entrypoint.sh`) — you don't run `manage.py migrate` by hand.

First build takes a while (PyTorch + sentence-transformers are large
downloads) — subsequent builds reuse Docker's layer cache and are much
faster unless `requirements.txt` changed.

## 6. Verify

```bash
docker compose ps          # all should show "Up" / "healthy"
docker compose logs -f web # watch it come up; Ctrl+C just detaches, doesn't stop it
curl -I http://localhost/
```

Create an admin user (one-off command in the running web container):
```bash
docker compose exec web python manage.py createsuperuser
```

Open `http://<your-vm-ip>/` — you should see the app.

## 7. Confirm it survives disconnecting

This is the actual thing you asked about — verify it, don't just trust it:

```bash
exit                       # close your SSH session entirely
# ... reconnect a minute later ...
ssh you@your-vm
docker compose ps          # still "Up" -- your session closing didn't touch it
curl -I http://localhost/  # still 200
```

And to confirm it survives a full VM reboot (do this once, deliberately,
not as a surprise):
```bash
sudo reboot
# wait ~30s, reconnect
ssh you@your-vm
docker compose ps          # containers back up on their own
systemctl status ollama    # Ollama back up on its own too
```

## 8. Updating / redeploying

```bash
cd siresoft-rag
git pull
docker compose up -d --build
```

`--build` only rebuilds layers that actually changed. Migrations run
automatically again on the new containers' startup.

## 9. Logs and troubleshooting

```bash
docker compose logs -f web       # Django/Gunicorn
docker compose logs -f worker    # Celery ingestion pipeline
docker compose logs -f nginx
docker compose logs -f db
journalctl -u ollama -f          # Ollama runs outside Compose, so its logs are separate
```

If `web` or `worker` can't reach Ollama, the usual cause is
`host.docker.internal` not resolving — confirm `extra_hosts:
host-gateway` support with:
```bash
docker compose exec web getent hosts host.docker.internal
```
If that fails, your Docker version may be too old for `host-gateway`
(needs 20.10+) — `docker --version` to check.

## 10. TLS (do this before exposing beyond a private network)

Put a reverse proxy with TLS in front of the `nginx` container — the
simplest path is running `certbot` against the host's own nginx/caddy in
front of this stack, or swapping the `nginx` service's image for one
with certbot baked in. Not included here since certificate provisioning
depends on your domain/DNS setup, but don't skip it before going public.
