"""Site-wide login gate.

Every page and API endpoint requires a logged-in, active user except the
login page itself, Django admin (which has its own login form and
is_staff gate), and static/media files. One middleware covering
everything was chosen over sprinkling LoginRequiredMixin / DRF permission
classes across every view -- simpler to audit, and impossible to
accidentally leave a new view unprotected.

This is also the enforcement point for admin-side deactivation: Django's
session auth only checks credentials once, at login -- it does not
re-check is_active on every later request. A user deactivated mid-session
(see core.admin's "Force logout and deactivate" action) keeps a live
session until *something* re-checks is_active; this middleware is that
check, run on their very next request.
"""

from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect

_EXEMPT_PREFIXES = ("/admin/", "/static/", "/media/")


class RequireActiveLoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == settings.LOGIN_URL or request.path.startswith(_EXEMPT_PREFIXES):
            return self.get_response(request)

        user = request.user
        if not user.is_authenticated or not user.is_active:
            if user.is_authenticated:
                logout(request)
            if request.path.startswith("/api/"):
                return JsonResponse({"detail": "Authentication required."}, status=401)
            return redirect(settings.LOGIN_URL)

        return self.get_response(request)
