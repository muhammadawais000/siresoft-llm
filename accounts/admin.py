"""User admin with the accounts app's additions: an "Access" section for
time-limited accounts, access columns in the user list, re-activation,
and force-logout-and-deactivate (bulk action + one-click row button).

Replaces the stock User admin; if the project registers its own User
admin, subclass AccountsUserAdmin instead of UserAdmin.
"""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.html import format_html

from accounts.models import UserAccess
from accounts.services import apply_admin_access_change, restart_access

User = get_user_model()

if admin.site.is_registered(User):
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
class AccountsUserAdmin(UserAdmin):
    list_display = (*UserAdmin.list_display, "is_active", "access_hours", "access_expires", "force_logout_button")
    actions = [*UserAdmin.actions, "force_logout_and_deactivate", "reactivate_and_restart_access"]
    inlines = [*UserAdmin.inlines, UserAccessInline]

    class Media:
        js = ("accounts/js/force_logout.js",)

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
        stamp = access.expires_at.strftime("%Y-%m-%d %H:%M")
        if access.is_expired():
            return format_html('<span style="color:#b91c1c;">Expired {}</span>', stamp)
        return stamp

    def save_model(self, request, obj, form, change):
        # Remembered for save_related(): the inline is saved after this, so
        # acting here would be overwritten by the inline form.
        request._accounts_reactivated = change and "is_active" in form.changed_data and obj.is_active
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        hours_changed = any(
            "access_hours" in f.changed_data for fs in formsets if fs.model is UserAccess for f in fs.forms
        )
        apply_admin_access_change(
            form.instance,
            reactivated=getattr(request, "_accounts_reactivated", False),
            hours_changed=hours_changed,
        )

    @admin.action(description="Re-activate selected users and restart their access hours")
    def reactivate_and_restart_access(self, request, queryset):
        count = 0
        for user in queryset:
            restart_access(user)
            count += 1
        self.message_user(request, f"Re-activated {count} user(s); each one's access hours start again from now.")

    @admin.action(description="Force logout and deactivate selected users")
    def force_logout_and_deactivate(self, request, queryset):
        count = queryset.update(is_active=False)
        # LoginRequiredMiddleware checks is_active on every request, so
        # each user is logged out on their very next one.
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
                name="accounts_user_force_logout",
            ),
        ]
        return custom + super().get_urls()

    def force_logout_view(self, request, user_id):
        # admin_view() enforces is_staff + login; CSRF still applies (the
        # button's fetch() sends X-CSRFToken -- see force_logout.js).
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
            "onclick=\"accountsForceLogout('{}', '{}')\">Logout</button>",
            f"{obj.pk}/force-logout/",
            obj.get_username(),
        )
