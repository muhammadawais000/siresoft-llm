#!/bin/bash
# Runs before every container start (web AND worker both use this image).
# Migrations are safe to run redundantly from two containers on startup --
# Django's migration executor is idempotent and takes a DB-level lock, so
# whichever of web/worker starts first just does the work and the other
# sees "no migrations to apply".
set -e

echo "Running migrations..."
python manage.py migrate --noinput

# Only the web (gunicorn) container serves static files via the shared
# volume -- running this from the worker container too would just race
# the same writes for no benefit.
if [[ "$1" == "gunicorn" ]]; then
  echo "Collecting static files..."
  python manage.py collectstatic --noinput
fi

echo "Starting: $*"
exec "$@"
