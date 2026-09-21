from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

# Signup is deliberately not a feature (see the login page) -- every
# account is created here, by an admin, via the stock "Add user" form
# UserAdmin already provides. This just adds the one extra workflow the
# brief calls for: force-logout-and-deactivate in a single action, rather
# than making an admin separately uncheck "Active" and remember that a
# deactivated user needs kicking out of any session they're already in.

admin.site.unregister(User)


@admin.register(User)
class SiresoftUserAdmin(UserAdmin):
    actions = [*UserAdmin.actions, "force_logout_and_deactivate"]

    @admin.action(description="Force logout and deactivate selected users")
    def force_logout_and_deactivate(self, request, queryset):
        count = queryset.update(is_active=False)
        # No session-table surgery needed here -- RequireActiveLoginMiddleware
        # checks is_active on every request, so each affected user is
        # logged out and bounced to the login page on their very next one.
        self.message_user(
            request,
            f"Deactivated {count} user(s). Each will be logged out on their next request "
            f"and can't log back in until reactivated here.",
        )
