"""Case-insensitive username login.

Django's default ModelBackend matches usernames exactly, case-sensitive -
a user created as "sunny" cannot log in typing "Sunny". This subclass only
changes the lookup to be case-insensitive (via __iexact); everything else
(password check, permission checks, is_active check) is inherited unchanged
from ModelBackend.

If two users ever exist whose usernames differ only by case (e.g. "sunny"
and "Sunny" as separate accounts), .get() below would raise
MultipleObjectsReturned - that's treated as a failed login (no crash) since
it's genuinely ambiguous which account was meant. Django's default username
field is unique only in an exact-case sense, so this situation is possible
in theory; if it happens in practice, the fix is to merge/rename the
duplicate account, not to guess here.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None
        UserModel = get_user_model()
        try:
            user = UserModel._default_manager.get(**{f"{UserModel.USERNAME_FIELD}__iexact": username})
        except UserModel.DoesNotExist:
            # Run the default password hasher anyway to avoid leaking via
            # response-time whether a username exists (same technique
            # ModelBackend itself uses).
            UserModel().set_password(password)
            return None
        except UserModel.MultipleObjectsReturned:
            return None
        else:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        return None
