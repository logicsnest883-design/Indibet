from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("IndiBet", {"fields": ("phone", "date_of_birth")}),
    )
    list_display = ("username", "email", "phone", "is_staff")