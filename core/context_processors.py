import hashlib
from functools import lru_cache

from django.contrib.staticfiles import finders

_VERSIONED = ("css/app.css", "js/app.js")


@lru_cache(maxsize=1)
def _static_version() -> str:
    # nginx serves /static/ with a 30-day cache and these filenames never
    # change, so without this a browser keeps running last month's app.js
    # after a deploy. A hash of their contents, added as ?v=..., changes
    # the URL whenever they do. Computed once per process -- a deploy
    # rebuilds the image and restarts it.
    digest = hashlib.md5()
    for name in _VERSIONED:
        path = finders.find(name)
        if path:
            with open(path, "rb") as f:
                digest.update(f.read())
    return digest.hexdigest()[:10]


def static_version(request):
    return {"STATIC_VERSION": _static_version()}
