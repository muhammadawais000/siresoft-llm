import hashlib
from functools import lru_cache

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.shortcuts import resolve_url
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import format_html

from accounts.conf import accounts_settings

register = template.Library()

_GUARD = "accounts/js/session-guard.js"


@lru_cache(maxsize=1)
def _guard_version():
    # Static files are often served with a long cache; a content hash in
    # the URL makes browsers pick up a new version after a deploy.
    path = finders.find(_GUARD)
    if not path:
        return ""
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:10]


@register.simple_tag
def accounts_session_guard():
    """<script> for signed-in pages: redirects to sign-in on any 401 and
    polls the account status. Place it before the page's own scripts."""
    return format_html(
        '<script src="{}?v={}" data-login-url="{}" data-status-url="{}" data-poll-seconds="{}"></script>',
        static(_GUARD),
        _guard_version(),
        resolve_url(settings.LOGIN_URL),
        reverse("accounts:status"),
        accounts_settings.STATUS_POLL_SECONDS,
    )
