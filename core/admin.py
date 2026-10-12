from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.html import format_html

from core.access import apply_admin_access_change, restart_access
from core.models import UserAccess

# Accounts come from self-signup (time-limited, see core.access) or from
# the stock "Add user" form here (no time limit). On top of UserAdmin this
# adds: force-logout-and-deactivate, both as a bulk action and as a
# one-click button per row; and the "Access" section, where an admin sets
# how many hours a time-limited account stays active.

admin.site.unregister(User)


class UserAccessInline(admin.StackedInline):
    model = UserAccess
    can_delete = False
    extra = 0
    fields = ("access_hours", "access_starts_at", "expires_at", "email_verified")
    readonly_fields = ("expires_at",)

    @admin.display(description="Expires at")
    def expires_at(self, obj):
        return obj.expires_at or "Never"


@admin.register(User)
class SiresoftUserAdmin(UserAdmin):
    list_display = (*UserAdmin.list_display, "is_active", "access_hours", "access_expires", "force_logout_button")
    actions = [*UserAdmin.actions, "force_logout_and_deactivate", "reactivate_and_restart_access"]
    inlines = [*UserAdmin.inlines, UserAccessInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("access")

    @admin.display(description="Access hours")
    def access_hours(self, obj):
        access = getattr(obj, "access", None)
        return access.access_hours if access and access.access_hours is not None else "No limit"

    @admin.display(description="Access expires")
    def access_expires(self, obj):
        access = getattr(obj, "access", None)
        if access is None or access.expires_at is None:
            return "Never"
        if access.is_expired():
            return format_html('<span style="color:#b91c1c;">Expired {}</span>', access.expires_at.strftime("%Y-%m-%d %H:%M"))
        return access.expires_at.strftime("%Y-%m-%d %H:%M")

    def save_model(self, request, obj, form, change):
        # Remembered for save_related(): the inline is saved after this, so
        # restarting the clock here would be overwritten by the inline form.
        request._siresoft_reactivated = change and "is_active" in form.changed_data and obj.is_active
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        hours_changed = any(
            "access_hours" in f.changed_data for fs in formsets if fs.model is UserAccess for f in fs.forms
        )
        apply_admin_access_change(
            form.instance,
            reactivated=getattr(request, "_siresoft_reactivated", False),
            hours_changed=hours_changed,
        )

    @admin.action(description="Re-activate selected users and restart their access hours")
    def reactivate_and_restart_access(self, request, queryset):
        count = 0
        for user in queryset:
            restart_access(user)
            count += 1
        self.message_user(request, f"Re-activated {count} user(s); each one's access hours start again from now.")

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
