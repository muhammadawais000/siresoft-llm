import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, resolve_url
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views import View
from django.views.generic import FormView

from accounts.conf import accounts_settings, message
from accounts.forms import EmailAuthenticationForm, SignupForm
from accounts.models import UserAccess
from accounts.services import get_access, remaining_text, send_verification_email, start_access_period

logger = logging.getLogger(__name__)
User = get_user_model()


class AccountsPageMixin:
    """Text every accounts page template needs, from accounts.conf."""

    page = None  # "login" or "signup"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["accounts"] = {
            "site_name": accounts_settings.SITE_NAME,
            "heading": message(f"{self.page}_heading"),
            "subtitle": message(f"{self.page}_subtitle"),
            "signup_enabled": accounts_settings.SIGNUP_ENABLED,
            "forgot_password_url": accounts_settings.FORGOT_PASSWORD_URL,
        }
        return context


class AccountsLoginView(AccountsPageMixin, LoginView):
    page = "login"
    template_name = "accounts/login.html"
    form_class = EmailAuthenticationForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        access = get_access(form.get_user())
        if access is not None and access.expires_at is not None:
            messages.success(self.request, message("welcome_hours", remaining=remaining_text(access)))
        return response


class SignupView(AccountsPageMixin, FormView):
    """Creates an inactive account and emails a verification link. The
    account (and its access period) only starts once that link is
    clicked -- see ActivateAccountView.
    """

    page = "signup"
    template_name = "accounts/signup.html"
    form_class = SignupForm

    def dispatch(self, request, *args, **kwargs):
        if not accounts_settings.SIGNUP_ENABLED:
            raise Http404
        if request.user.is_authenticated:
            return redirect(settings.LOGIN_REDIRECT_URL)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        email = form.cleaned_data["email"]
        user = form.existing_unverified
        created = user is None
        with transaction.atomic():
            if created:
                user = User.objects.create_user(username=email, email=email, is_active=False)
                UserAccess.objects.create(
                    user=user, access_hours=accounts_settings.SIGNUP_ACCESS_HOURS, email_verified=False
                )
            user.set_password(form.cleaned_data["password1"])
            user.save(update_fields=["password"])
        try:
            send_verification_email(self.request, user, force=created)
        except Exception:
            logger.exception("could not send verification email to user=%s", user.pk)
            if created:
                user.delete()
            form.add_error(None, message("email_send_failed"))
            return self.form_invalid(form)
        messages.success(self.request, message("signup_done", email=email))
        return redirect(settings.LOGIN_URL)


class ActivateAccountView(View):
    """The link in the "account created" email. Verifies the email, turns
    the account on, starts the access clock, and lands on the sign-in page.
    """

    def get(self, request, uidb64, token):
        try:
            user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            user = None
        access = get_access(user) if user is not None else None

        if user is None or access is None or not default_token_generator.check_token(user, token):
            messages.error(request, message("activation_invalid"))
        elif access.email_verified:
            # Already used: never let an old link re-activate an account
            # that has since expired or been turned off by an admin.
            messages.info(request, message("already_verified"))
        else:
            with transaction.atomic():
                access.email_verified = True
                start_access_period(access)
                access.save()
                user.is_active = True
                user.save(update_fields=["is_active"])
            messages.success(request, message("verified"))
        return redirect(resolve_url(settings.LOGIN_URL))


class AccountStatusView(View):
    """GET -- polled by the session guard script so a user an admin turns
    off is logged out within seconds. LoginRequiredMiddleware answers 401
    before this view runs once the account is inactive or out of time.
    """

    def get(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({"detail": message("api_unauthenticated")}, status=401)
        access = get_access(request.user)
        expires_at = access.expires_at if access is not None else None
        return JsonResponse({"active": True, "expires_at": expires_at.isoformat() if expires_at else None})
