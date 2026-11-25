from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("IndiBet", {"fields": ("phone", "date_of_birth", "referral_code", "referred_by")}),
    )
    list_display = ("username", "email", "phone", "referred_by", "referral_total", "is_staff")
    list_select_related = ("referred_by",)
    search_fields = UserAdmin.search_fields + ("phone", "referral_code")
    readonly_fields = ("referral_code",)
    raw_id_fields = ("referred_by",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_referral_total=Count("referrals"))

    @admin.display(description="Referrals", ordering="_referral_total")
    def referral_total(self, obj):
        return obj._referral_total