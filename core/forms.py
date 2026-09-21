from django import forms
from django.contrib.auth.forms import AuthenticationForm


class EmailAuthenticationForm(AuthenticationForm):
    """AuthenticationForm relabeled for email login.

    The field is still named `username` (Django's AuthenticationForm.clean()
    passes it to authenticate() as the `username` kwarg regardless of
    label) -- only the label, widget, and validation look like an email
    field. core.auth_backends.EmailBackend is what actually treats the
    value as an email address.
    """

    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"class": "input", "autofocus": True, "placeholder": "you@company.com"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].widget.attrs.update({"class": "input", "placeholder": "••••••••"})

    def get_invalid_login_error(self):
        # Deliberately the same message for "no such account", "wrong
        # password", and "deactivated account" -- EmailBackend already
        # returns None for all three (see its user_can_authenticate()
        # check), so there's no way to tell them apart here anyway, and
        # not trying to is the standard practice (don't reveal which part
        # of the credential pair was wrong, or that an account exists but
        # is disabled).
        return forms.ValidationError(
            "Invalid email or password, or this account is inactive.",
            code="invalid_login",
        )
