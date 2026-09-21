from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.html import format_html

# Signup is deliberately not a feature (see the login page) -- every
# account is created here, by an admin, via the stock "Add user" form
# UserAdmin already provides. This just adds the workflow the brief calls
# for: force-logout-and-deactivate, both as a bulk action and as a
# one-click button per row, rather than making an admin separately
# uncheck "Active" and remember that a deactivated user needs kicking out
# of any session they're already in.

admin.site.unregister(User)


@admin.register(User)
class SiresoftUserAdmin(UserAdmin):
    list_display = (*UserAdmin.list_display, "force_logout_button")
    actions = [*UserAdmin.actions, "force_logout_and_deactivate"]

    class Media:
        js = ("admin/js/force_logout.js",)

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

    def get_urls(self):
        custom = [
            path(
                "<int:user_id>/force-logout/",
                self.admin_site.admin_view(self.force_logout_view),
                name="auth_user_force_logout",
            ),
        ]
        return custom + super().get_urls()

    def force_logout_view(self, request, user_id):
        # admin_view() already enforces is_staff + the standard session
        # login; CsrfViewMiddleware (global, see settings.MIDDLEWARE) still
        # applies on top of that -- the button's fetch() call carries the
        # X-CSRFToken header for exactly that reason (see force_logout.js).
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        user = get_object_or_404(User, pk=user_id)
        user.is_active = False
        user.save(update_fields=["is_active"])
        return JsonResponse({"status": "deactivated"})

    @admin.display(description="Force logout")
    def force_logout_button(self, obj):
        if not obj.is_active:
            return format_html('<span style="color:#94a3b8;">Inactive</span>')
        return format_html(
            '<button type="button" class="button" style="color:#b91c1c;" '
            "onclick=\"siresoftForceLogout({}, '{}')\">Logout</button>",
            obj.pk,
            obj.username,
        )
