import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import FormView, TemplateView

from core.access import get_access, remaining_text, send_verification_email, start_access_period
from core.forms import EmailAuthenticationForm, SignupForm
from core.models import UserAccess

logger = logging.getLogger(__name__)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class IndexView(TemplateView):
    """The single-page app shell. Everything else is driven client-side
    against the DRF/SSE API — this view has no context to build.

    ensure_csrf_cookie: the SPA's own POST/PUT/DELETE fetch calls (see
    static/js/app.js) read the csrftoken cookie themselves rather than
    templating a token in, since none of those calls originate from a
    server-rendered <form>. Without this decorator the cookie is only set
    once *something* calls get_token(request) (e.g. a template using
    {% csrf_token %}), which this template never does.
    """

    template_name = "index.html"

    def get_context_data(self, **kwargs):
        # The SPA doesn't render Django messages itself; hand them to
        # app.js, which shows them as toasts (e.g. the access-hours notice
        # set at login).
        context = super().get_context_data(**kwargs)
        context["initial_messages"] = [
            {"text": str(m), "type": "error" if m.level_tag == "error" else "info"}
            for m in messages.get_messages(self.request)
        ]
        return context


class SiresoftLoginView(LoginView):
    template_name = "registration/login.html"
    form_class = EmailAuthenticationForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        access = get_access(form.get_user())
        if access is not None and access.expires_at is not None:
            messages.success(
                self.request,
                f"Welcome! You have {remaining_text(access)} of usage on your account.",
            )
        return response


class AuthStatusView(View):
    """GET /api/auth/status/ -- polled by app.js every few seconds so a
    user whose access is turned off in the admin panel is logged out right
    away, without having to click or refresh anything. The checks
    themselves live in RequireActiveLoginMiddleware, which answers 401
    before this view runs once the account is inactive or out of time.
    """

    def get(self, request):
        access = get_access(request.user)
        expires_at = access.expires_at if access is not None else None
        return JsonResponse({"active": True, "expires_at": expires_at.isoformat() if expires_at else None})


class SignupView(FormView):
    """Self-signup: creates an inactive account and emails a verification
    link. The account (and its access period) only starts once that link
    is clicked -- see ActivateAccountView.
    """

    template_name = "registration/signup.html"
    form_class = SignupForm

    def dispatch(self, request, *args, **kwargs):
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
                    user=user, access_hours=settings.SIGNUP_ACCESS_HOURS, email_verified=False
                )
            user.set_password(form.cleaned_data["password1"])
            user.save(update_fields=["password"])
        try:
            send_verification_email(self.request, user, force=created)
        except Exception:
            logger.exception("could not send verification email to user=%s", user.pk)
            if created:
                user.delete()
            form.add_error(None, "We couldn't send the verification email. Please try again in a few minutes.")
            return self.form_invalid(form)
        messages.success(
            self.request,
            f"Account created. We've sent a link to {email} -- click it to verify your email, then sign in.",
        )
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
            messages.error(request, "This link is invalid or has expired. Sign up again to get a new one.")
        elif access.email_verified:
            # Already used: never let an old link re-activate an account
            # that has since expired or been turned off by an admin.
            messages.info(request, "Your email is already verified. Sign in below.")
        else:
            with transaction.atomic():
                access.email_verified = True
                start_access_period(access)
                access.save()
                user.is_active = True
                user.save(update_fields=["is_active"])
            messages.success(request, "Your email is verified. Sign in to start using Siresoft LLM.")
        return redirect(settings.LOGIN_URL)
