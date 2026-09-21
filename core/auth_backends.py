"""Email-based login on top of Django's stock User model.

No custom AUTH_USER_MODEL: Document.uploaded_by and everything else
already has migrations against `auth.User`, and swapping the user model
after the fact is a disruptive, largely unsupported change. Instead, the
login form's field is still called `username` (Django's standard
AuthenticationForm field name -- see core.forms.EmailAuthenticationForm),
and this backend just resolves whatever was typed into it to a user by
email before checking the password.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

UserModel = get_user_model()


class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None
        try:
            user = UserModel.objects.get(email__iexact=username)
        except (UserModel.DoesNotExist, UserModel.MultipleObjectsReturned):
            return None
        # user_can_authenticate() (inherited from ModelBackend) rejects
        # is_active=False here -- this is the enforcement point that keeps
        # a deactivated user from logging back in at all, independent of
        # the RequireActiveLoginMiddleware check that logs out a session
        # that predates the deactivation.
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
