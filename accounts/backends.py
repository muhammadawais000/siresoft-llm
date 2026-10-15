"""Sign in with email + password on Django's standard user model.

The sign-in form's field is still called `username` (what Django's
AuthenticationForm passes to authenticate()); this backend resolves the
value to a user by email before checking the password. Keep
django.contrib.auth.backends.ModelBackend after it in
AUTHENTICATION_BACKENDS so the Django admin's own username login works.
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
        # user_can_authenticate() rejects is_active=False: a deactivated
        # user can't sign in, independent of LoginRequiredMiddleware
        # logging out a session that predates the deactivation.
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
