from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from ares.api.models import User, InterviewSession, InterviewTurn   # ✅ import 추가


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "name", "is_staff")
    search_fields = ("email", "name")
    ordering = ("email",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Personal info",
            {"fields": ("name", "gender", "birth", "phone_number")},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password", "password2"),
            },
        ),
    )


# === Interview Models 등록 ===
@admin.register(InterviewSession)
class InterviewSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "started_at", "finished_at")
    search_fields = ("id", "report_id", "meta")
    list_filter = ("status",)
    ordering = ("-started_at",)


@admin.register(InterviewTurn)
class InterviewTurnAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "turn_index", "role", "created_at")
    search_fields = ("session__id", "question", "answer")
    list_filter = ("role",)
    ordering = ("session", "turn_index")
