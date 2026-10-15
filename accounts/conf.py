"""Every setting and user-facing message of the accounts app, with defaults.

A project changes them in one dict in its settings.py, e.g.:

    ACCOUNTS = {
        "SITE_NAME": "Acme Portal",
        "SIGNUP_ACCESS_HOURS": 48,
        "MESSAGES": {"login_heading": "Welcome back to {site_name}"},
    }

Keys left out keep the defaults below. Values are read on every access
(not cached at import), so override_settings works in tests.
"""

from django.conf import settings

DEFAULTS = {
    # Shown in page headings and emails.
    "SITE_NAME": "our site",
    # Show the sign-up page and link. False = accounts are only created by admins.
    "SIGNUP_ENABLED": True,
    # Hours a self-signup account stays active after verifying its email.
    # None = no time limit. Admins change it per user afterwards.
    "SIGNUP_ACCESS_HOURS": 24,
    # Who inactive users are told to contact. None = DEFAULT_FROM_EMAIL.
    "ADMIN_CONTACT_EMAIL": None,
    # Require sign-in for every page except the ones below and the
    # accounts pages themselves. False = only enforce access expiry.
    "LOGIN_REQUIRED": True,
    # Path prefixes that never require sign-in.
    "PUBLIC_PATH_PREFIXES": ("/static/", "/media/", "/admin/"),
    # Path prefixes answered with a 401 JSON body instead of a redirect.
    "API_PATH_PREFIXES": ("/api/",),
    # How often the session guard script checks that the account is
    # still active (seconds).
    "STATUS_POLL_SECONDS": 10,
    # Minimum gap between two verification / "account inactive" emails
    # to the same user (minutes), so repeated attempts can't flood an inbox.
    "NOTICE_THROTTLE_MINUTES": 5,
    # Link target for "Forgot your password?" on the sign-in page. None = hidden.
    "FORGOT_PASSWORD_URL": None,
    # Overrides for any key of MESSAGES below.
    "MESSAGES": {},
}

# {site_name}, {admin_email}, {email} and {remaining} are filled in where used.
MESSAGES = {
    "login_heading": "Sign in to {site_name}",
    "login_subtitle": "Sign in to your account",
    "signup_heading": "Sign up for {site_name}",
    "signup_subtitle": "Create your account",
    "invalid_login": "Invalid email or password.",
    "unverified_login": "Please verify your email first. Check your inbox for the link we sent you.",
    "inactive_login": "Your account is inactive. We've emailed you how to get it re-activated.",
    "inactive_logout": "Your account is now inactive. Contact {admin_email} to have it re-activated.",
    "welcome_hours": "Welcome! You have {remaining} of usage on your account.",
    "email_exists": "An account with this email already exists. Sign in instead.",
    "password_mismatch": "The two passwords don't match.",
    "email_send_failed": "We couldn't send the verification email. Please try again in a few minutes.",
    "signup_done": "Account created. We've sent a link to {email} -- click it to verify your email, then sign in.",
    "activation_invalid": "This link is invalid or has expired. Sign up again to get a new one.",
    "already_verified": "Your email is already verified. Sign in below.",
    "verified": "Your email is verified. Sign in to start using {site_name}.",
    "created_email_subject": "Your {site_name} account has been created",
    "inactive_email_subject": "Your {site_name} account is inactive",
    "api_unauthenticated": "Authentication required.",
}


class _AccountsSettings:
    def __getattr__(self, name):
        if name not in DEFAULTS:
            raise AttributeError(f"Unknown accounts setting: {name}")
        return getattr(settings, "ACCOUNTS", {}).get(name, DEFAULTS[name])

    @property
    def admin_email(self):
        return self.ADMIN_CONTACT_EMAIL or settings.DEFAULT_FROM_EMAIL


accounts_settings = _AccountsSettings()


def message(key, **values):
    """A user-facing message, with the project's overrides applied."""
    text = {**MESSAGES, **accounts_settings.MESSAGES}[key]
    return text.format(site_name=accounts_settings.SITE_NAME, admin_email=accounts_settings.admin_email, **values)
