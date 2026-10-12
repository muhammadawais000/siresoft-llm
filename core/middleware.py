"""Site-wide login gate.

Every page and API endpoint requires a logged-in, active user except the
login, signup and email-verification pages, Django admin (which has its own login form and
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
from django.contrib import messages
from django.contrib.auth import SESSION_KEY, logout
from django.http import JsonResponse
from django.shortcuts import redirect

from core.access import access_expired, expire_user

_EXEMPT_PREFIXES = ("/admin/", "/static/", "/media/", "/activate/")
_EXEMPT_PATHS = (settings.LOGIN_URL, "/signup/")


class RequireActiveLoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in _EXEMPT_PATHS or request.path.startswith(_EXEMPT_PREFIXES):
            return self.get_response(request)

        user = request.user
        # A time-limited account whose period ran out since the last
        # core.tasks.expire_user_access run is expired here, on the spot.
        if user.is_authenticated and user.is_active and access_expired(user):
            expire_user(user)
        if not user.is_authenticated or not user.is_active:
            # A deactivated user arrives here already anonymous (Django's
            # backend refuses to load an inactive user from the session),
            # so a user id still in the session is what says "this was a
            # signed-in user who just lost access" rather than a visitor.
            if user.is_authenticated or SESSION_KEY in request.session:
                logout(request)
                messages.info(
                    request,
                    f"Your account is now inactive. Contact {settings.ADMIN_CONTACT_EMAIL} "
                    f"to have it re-activated.",
                )
            if request.path.startswith("/api/"):
                return JsonResponse({"detail": "Authentication required."}, status=401)
            return redirect(settings.LOGIN_URL)

        return self.get_response(request)
