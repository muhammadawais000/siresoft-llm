#!/bin/bash
# Runs before every container start (web AND worker both use this image).
# Migrations only run from the web (gunicorn) container, not worker --
# Django's migration executor does NOT take a DB-level lock, so if web and
# worker both start at once and both see a migration as unapplied, they
# can race the same DDL (e.g. both issuing ADD COLUMN) and one crashes with
# a duplicate-column/index error. Restricting migrate to one container
# removes the race entirely, the same reasoning as collectstatic below.
set -e

if [[ "$1" == "gunicorn" ]]; then
  echo "Running migrations..."
  python manage.py migrate --noinput

  # Only the web (gunicorn) container serves static files via the shared
  # volume -- running this from the worker container too would just race
  # the same writes for no benefit.
  echo "Collecting static files..."
  python manage.py collectstatic --noinput
fi

echo "Starting: $*"
exec "$@"
