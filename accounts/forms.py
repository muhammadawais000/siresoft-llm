from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password

from accounts.conf import message
from accounts.services import (
    access_expired_for,
    expire_user,
    get_access,
    send_inactive_notice,
    send_verification_email,
)

User = get_user_model()

# Widgets carry the app's own CSS classes (styled by
# static/accounts/css/accounts.css, or by the project's own stylesheet
# when it overrides accounts/layout.html).
_INPUT = {"class": "acc-input"}


class EmailAuthenticationForm(AuthenticationForm):
    """AuthenticationForm relabeled for email sign-in (see backends.EmailBackend)."""

    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={**_INPUT, "autofocus": True, "placeholder": "you@company.com"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].widget.attrs.update({**_INPUT, "placeholder": "••••••••"})

    def clean(self):
        # The stock clean() can't tell an inactive account from a wrong
        # password (EmailBackend returns None for both). Once the password
        # is proven correct, an inactive user is told why and emailed what
        # to do -- but only then, so typing someone else's email can't be
        # used to learn whether it has an account or to spam its inbox.
        email = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if email and password:
            user = User.objects.filter(email__iexact=email).order_by("pk").first()
            if user is not None and user.check_password(password):
                if user.is_active and access_expired_for(user):
                    expire_user(user)
                if not user.is_active:
                    self._raise_for_inactive(user)
        return super().clean()

    def _raise_for_inactive(self, user):
        access = get_access(user)
        if access is not None and not access.email_verified:
            try:
                send_verification_email(self.request, user)
            except Exception:
                pass
            raise forms.ValidationError(message("unverified_login"), code="unverified")
        send_inactive_notice(user)
        raise forms.ValidationError(message("inactive_login"), code="inactive")

    def get_invalid_login_error(self):
        # Same message for "no such account" and "wrong password" -- don't
        # reveal which part of the pair was wrong.
        return forms.ValidationError(message("invalid_login"), code="invalid_login")


class SignupForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={**_INPUT, "autofocus": True, "placeholder": "you@company.com"}),
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={**_INPUT, "placeholder": "••••••••", "autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(attrs={**_INPUT, "placeholder": "••••••••", "autocomplete": "new-password"}),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        existing = User.objects.filter(email__iexact=email).first()
        if existing is not None:
            access = get_access(existing)
            # An account that never verified its email can sign up again
            # (new password, fresh link); any other account already exists.
            if access is None or access.email_verified:
                raise forms.ValidationError(message("email_exists"))
        self.existing_unverified = existing
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", message("password_mismatch"))
        elif p1 and cleaned.get("email"):
            try:
                validate_password(p1, User(username=cleaned["email"], email=cleaned["email"]))
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned
