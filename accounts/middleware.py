"""Site-wide sign-in gate and access-expiry enforcement.

Every page requires a signed-in, active user except the accounts pages
and PUBLIC_PATH_PREFIXES (with LOGIN_REQUIRED=False, only expiry is
enforced). API_PATH_PREFIXES and the account status endpoint get a 401
JSON body instead of a redirect.

Django's session auth only checks credentials at sign-in; this is what
logs out a user who was deactivated, or whose access hours ran out,
mid-session -- on their very next request.

Must come after AuthenticationMiddleware and MessageMiddleware.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import SESSION_KEY, logout
from django.http import JsonResponse
from django.shortcuts import redirect, resolve_url
from django.urls import reverse

from accounts.conf import accounts_settings, message
from accounts.services import access_expired_for, expire_user


def _public_paths():
    return {reverse("accounts:login"), reverse("accounts:signup"), reverse("accounts:logout")}


def _activate_prefix():
    return reverse("accounts:activate", args=["uid", "token"]).split("uid/")[0]


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if (
            path in _public_paths()
            or path.startswith(_activate_prefix())
            or path.startswith(tuple(accounts_settings.PUBLIC_PATH_PREFIXES))
        ):
            return self.get_response(request)

        user = request.user
        if user.is_authenticated and user.is_active and access_expired_for(user):
            expire_user(user)

        # A deactivated user arrives here already anonymous (Django's
        # backend refuses to load an inactive user from the session), so a
        # user id still in the session is what says "this was a signed-in
        # user who just lost access" rather than a visitor.
        lost_access = (user.is_authenticated and not user.is_active) or (
            not user.is_authenticated and SESSION_KEY in request.session
        )
        if lost_access:
            logout(request)
            messages.info(request, message("inactive_logout"))
        elif user.is_authenticated or not accounts_settings.LOGIN_REQUIRED:
            return self.get_response(request)

        if path.startswith(tuple(accounts_settings.API_PATH_PREFIXES)) or path == reverse("accounts:status"):
            return JsonResponse({"detail": message("api_unauthenticated")}, status=401)
        return redirect(resolve_url(settings.LOGIN_URL))
