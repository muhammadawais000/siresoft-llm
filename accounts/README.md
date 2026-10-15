# accounts: sign-in, sign-up and time-limited access for Django

A drop-in Django app that gives a project:

- **Email + password sign-in** (on Django's standard `User` model).
- **Self sign-up** with email verification. Signing up creates an inactive account and emails a link. Clicking the link activates the account and opens the sign-in page.
- **Time-limited access.** A self-signup account stays active for a set number of hours (24 by default) after the user verifies their email. Admins change the hours per user in the Django admin.
- **Automatic expiry.** When the hours run out, the account is deactivated and the user is logged out within seconds. A signal lets your project delete the user's data. If the user tries to sign in again, they are emailed to contact the admin.
- **Admin tools.** An "Access" section on each user, access columns in the user list, re-activation, and force-logout.
- **Site-wide sign-in gate.** Every page requires sign-in, except the ones you mark public. API paths get a `401` JSON response instead of a redirect.

Everything is configured from one `ACCOUNTS` dict in `settings.py`. That includes every user-facing message and email subject.

---

## Requirements

- Django 4.2 or newer (developed on 5.0).
- Django's default user model, or a custom one that still has `username` and `email` fields. Sign-up sets `username` to the email address.
- A working email setup for real sign-ups (see step 7).
- Optional: Celery with beat, to expire accounts on a schedule. Without Celery, use cron (see step 9).

---

## Setup

### 1. Copy the app

Copy the whole `accounts/` folder into your project, next to `manage.py`.

### 2. `INSTALLED_APPS`

Add the app **after** `django.contrib.admin` and `django.contrib.auth`:

```python
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # ...
    "accounts",
    # ...your apps
]
```

The app replaces the stock User admin with its own. If your project registers a custom User admin, subclass `accounts.admin.AccountsUserAdmin` instead of `UserAdmin`.

### 3. `MIDDLEWARE`

Add the gate **after** `AuthenticationMiddleware` **and** `MessageMiddleware`:

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "accounts.middleware.LoginRequiredMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
```

### 4. Authentication backends and redirects

```python
AUTHENTICATION_BACKENDS = [
    "accounts.backends.EmailBackend",               # sign in by email
    "django.contrib.auth.backends.ModelBackend",    # keeps the admin's username login working
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "/"                 # where users land after signing in
LOGOUT_REDIRECT_URL = "accounts:login"
```

### 5. URLs

In the project's `urls.py`:

```python
from django.urls import include, path

urlpatterns = [
    path("", include("accounts.urls")),
    path("admin/", admin.site.urls),
    # ...
]
```

This adds:

| URL | Name | Page |
|---|---|---|
| `/login/` | `accounts:login` | Sign in |
| `/logout/` | `accounts:logout` | Sign out (POST) |
| `/signup/` | `accounts:signup` | Sign up |
| `/activate/<uid>/<token>/` | `accounts:activate` | Link in the verification email |
| `/account/status/` | `accounts:status` | JSON status, polled by the session guard |

To put them under a prefix, use `path("accounts/", include("accounts.urls"))` instead. Everything else follows automatically because the app uses URL names, not hard-coded paths.

A sign-out button must POST:

```html
<form method="post" action="{% url 'accounts:logout' %}">{% csrf_token %}<button>Sign out</button></form>
```

### 6. `ACCOUNTS` settings

Every key is optional. Leave out a key to keep its default.

```python
ACCOUNTS = {
    "SITE_NAME": "Acme Portal",
    "SIGNUP_ACCESS_HOURS": 24,
    "ADMIN_CONTACT_EMAIL": "admin@acme.com",
}
```

| Key | Default | What it does |
|---|---|---|
| `SITE_NAME` | `"our site"` | Used in page headings and emails. |
| `SIGNUP_ENABLED` | `True` | `False` hides the sign-up page and link. Accounts are then created only by admins. |
| `SIGNUP_ACCESS_HOURS` | `24` | Hours a self-signup account stays active after its email is verified. `None` means no time limit. |
| `ADMIN_CONTACT_EMAIL` | `DEFAULT_FROM_EMAIL` | Who inactive users are told to contact. |
| `LOGIN_REQUIRED` | `True` | Require sign-in for every page. With `False`, only access expiry is enforced. |
| `PUBLIC_PATH_PREFIXES` | `("/static/", "/media/", "/admin/")` | Paths that never require sign-in. The accounts pages are always public. |
| `API_PATH_PREFIXES` | `("/api/",)` | Paths that get a `401` JSON response instead of a redirect to sign-in. |
| `STATUS_POLL_SECONDS` | `10` | How often a signed-in page checks that the account is still active. `0` turns polling off. |
| `NOTICE_THROTTLE_MINUTES` | `5` | Minimum gap between two emails to the same user, so repeated attempts can't flood an inbox. |
| `FORGOT_PASSWORD_URL` | `None` | Link target for "Forgot your password?" on the sign-in page. `None` hides the link. |
| `MESSAGES` | `{}` | Overrides for any user-facing text (see below). |

#### Messages

Every text the app shows or emails lives in `accounts/conf.py` → `MESSAGES`. Override any of them:

```python
ACCOUNTS = {
    "SITE_NAME": "Acme Portal",
    "MESSAGES": {
        "login_heading": "Welcome back to {site_name}",
        "inactive_logout": "Your trial has ended. Write to {admin_email} to continue.",
    },
}
```

The placeholders are `{site_name}` and `{admin_email}` everywhere, plus `{email}` in `signup_done` and `{remaining}` in `welcome_hours`.

| Key | Shown when |
|---|---|
| `login_heading`, `login_subtitle` | Sign-in page heading and subtitle |
| `signup_heading`, `signup_subtitle` | Sign-up page heading and subtitle |
| `invalid_login` | Wrong email or password |
| `unverified_login` | Correct password, but the email isn't verified yet (a new link is sent) |
| `inactive_login` | Correct password, but the account is inactive (the "contact admin" email is sent) |
| `inactive_logout` | A signed-in user was logged out because their account went inactive |
| `welcome_hours` | After sign-in, for time-limited accounts |
| `email_exists` | Sign-up with an email that already has an account |
| `password_mismatch` | Sign-up passwords don't match |
| `email_send_failed` | The verification email couldn't be sent |
| `signup_done` | After a successful sign-up |
| `activation_invalid` | Verification link is wrong or expired (after 3 days, Django's `PASSWORD_RESET_TIMEOUT`) |
| `already_verified` | Verification link clicked a second time |
| `verified` | Verification link clicked successfully |
| `created_email_subject` | Subject of the verification email |
| `inactive_email_subject` | Subject of the "account inactive" email |
| `api_unauthenticated` | `detail` of the `401` JSON response |

### 7. Email

The app sends mail with Django's standard email settings. For example, for Gmail (this needs an App Password, not the normal account password):

```python
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = "you@gmail.com"
EMAIL_HOST_PASSWORD = "your-app-password"
DEFAULT_FROM_EMAIL = "Acme Portal <you@gmail.com>"
EMAIL_TIMEOUT = 15
```

With `django.core.mail.backends.console.EmailBackend`, emails are only printed to the server log. That's fine for development, but real users can't verify their accounts.

Links in emails are built from the incoming request's host. Behind a proxy, make sure it forwards the `Host` header, and that the host is in `ALLOWED_HOSTS`.

### 8. Migrate

```bash
python manage.py migrate accounts
```

### 9. Schedule expiry

Accounts also expire on the user's next request or sign-in attempt. A scheduled job makes it happen on time, so the user's data is cleaned up even if they never come back. Use one of these.

**Celery beat:**

```python
CELERY_BEAT_SCHEDULE = {
    "expire-user-access": {"task": "accounts.tasks.expire_user_access", "schedule": 300.0},  # every 5 minutes
}
```

Make sure beat is running, e.g. `celery -A yourproject worker -B` or a separate `celery -A yourproject beat` process.

**Cron (no Celery):**

```cron
*/5 * * * * cd /path/to/project && python manage.py expire_accounts
```

### 10. Add the session guard to signed-in pages

In the template of every signed-in page, before the page's own scripts:

```html
{% load accounts %}
{% accounts_session_guard %}
<script src="{% static 'js/your-app.js' %}"></script>
```

It renders one `<script>` tag that does two things:

- Any `fetch()` that comes back `401` sends the browser to the sign-in page, where the reason is shown.
- The page checks the account status every `STATUS_POLL_SECONDS`, and again when a hidden tab is shown. If an admin turns the account off, the user is logged out within seconds.

The script URL includes a content hash (`?v=...`), so browsers pick up new versions even when static files are cached for a long time.

Only `fetch()` calls are covered. If your pages use `XMLHttpRequest` or a library built on it (for example axios), handle `401` there yourself.

### 11. Clean up user data when access expires

The app doesn't know what data your project keeps per user. Connect to the `access_expired` signal to remove it, for example in your app's `apps.py`:

```python
from django.apps import AppConfig


class ChatConfig(AppConfig):
    name = "chat"

    def ready(self):
        from accounts.signals import access_expired
        from chat.signals import delete_chat_history

        access_expired.connect(delete_chat_history, dispatch_uid="chat_delete_history_on_access_expired")
```

```python
# chat/signals.py
from chat.models import ChatSession


def delete_chat_history(sender, user, **kwargs):
    ChatSession.objects.filter(user=user).delete()
```

The signal is sent inside the same database transaction that deactivates the user.

### 12. Branding (optional)

Out of the box, the pages use a plain centered card with the app's own stylesheet (`accounts/static/accounts/css/accounts.css`). There are three levels of customization:

1. **Colors only.** Override the CSS variables (`--acc-primary`, `--acc-bg`, ...) in your own stylesheet.
2. **Your own page shell.** Create `templates/accounts/layout.html` in your project. It replaces the app's layout for both pages. It must:
   - render `{{ accounts.heading }}` and `{{ accounts.subtitle }}`,
   - `{% include "accounts/includes/messages.html" %}` (flash messages and form errors),
   - contain `{% block accounts_content %}{% endblock %}` (the form).

   Your stylesheet then styles the app's class names: `acc-form`, `acc-field`, `acc-label`, `acc-input`, `acc-field-error`, `acc-forgot`, `acc-btn`, `acc-switch`, `acc-link`, `acc-alert`, `acc-alert-error`, `acc-alert-success`.
3. **Individual templates.** Override `accounts/login.html`, `accounts/signup.html` or `accounts/includes/field.html` the same way.

Email bodies are templates too: override `templates/accounts/emails/account_created.txt` and `account_inactive.txt`. The variables available are `site_name`, `admin_email`, and in the verification email `activation_url` and `access_hours`.

### 13. Run the tests

```bash
python manage.py test accounts
```

The tests use their own URLs and settings, so they run in any project.

---

## Using it day to day (admin)

Open **Admin → Users**.

| To... | Do this |
|---|---|
| See who is time-limited | Look at the **Access hours** and **Access expires** columns. Expired users show in red. |
| Give a user more or fewer hours | Open the user, change **Access → Access hours**, and save. The hours count from the original start. |
| End someone's access now | Set **Access hours** to `0` and save. They are logged out within `STATUS_POLL_SECONDS` and the `access_expired` signal fires. |
| Re-activate an expired user | Set their **Access hours** (e.g. `24`) and save, or tick **Active** and save, or use the **"Re-activate selected users and restart their access hours"** action. Their hours count from now. |
| Block someone without the time logic | Use the **Logout** button on their row, or the **"Force logout and deactivate"** action. |
| Make an account unlimited | Clear **Access hours** (leave it blank). Users created through **Add user** are unlimited by default. |

Superusers never expire.

---

## How it works

```
Sign up ──► inactive account + "account created" email
               │
               ▼  user clicks the link
       account active, access clock starts ──► sign in ──► "You have 24 hours of usage"
               │
               ▼  hours run out (scheduled job, next request, sign-in attempt, or admin sets 0)
       account inactive ──► access_expired signal (your cleanup)
               │                 └─► open pages are logged out by the session guard
               ▼
       sign-in attempt with the right password ──► "account inactive" email (contact admin)
               │
               ▼  admin gives hours or ticks Active
       account active again, clock restarts from now
```

| File | Contents |
|---|---|
| `conf.py` | Defaults for every setting and message |
| `models.py` | `UserAccess`: hours, start time, email verified (one per time-limited user) |
| `services.py` | Lifecycle logic: expire, restart, admin changes, emails |
| `signals.py` | `access_expired` |
| `backends.py` | `EmailBackend` |
| `forms.py`, `views.py`, `urls.py` | Sign-in, sign-up, verification and status pages |
| `middleware.py` | `LoginRequiredMiddleware`: sign-in gate and expiry on each request |
| `admin.py` | User admin with the Access section, columns and actions |
| `tasks.py`, `management/commands/expire_accounts.py` | Scheduled expiry (Celery or cron) |
| `templatetags/accounts.py`, `static/accounts/js/session-guard.js` | `{% accounts_session_guard %}` |
| `templates/accounts/` | Pages and email bodies (all overridable) |

---

## Troubleshooting

| Problem | Likely cause |
|---|---|
| Users never get the verification email | `EMAIL_BACKEND` is still the console backend, or the SMTP details are wrong. Check the server log: with the console backend, the email is printed there. |
| Link in the email points to the wrong host | Your proxy isn't forwarding `Host`, or the host isn't in `ALLOWED_HOSTS`. |
| A user isn't logged out after their hours are set to 0 | The page doesn't include `{% accounts_session_guard %}`, or the browser still has an old cached page. One refresh fixes it, and later versions are picked up automatically. |
| `MessageFailure` error | `accounts.middleware.LoginRequiredMiddleware` is above `MessageMiddleware` in `MIDDLEWARE`. Move it below. |
| Admin login stops working | `django.contrib.auth.backends.ModelBackend` is missing from `AUTHENTICATION_BACKENDS`. |
| Accounts never expire on schedule | Celery beat isn't running, or the cron job isn't installed. They still expire on the user's next request. |
