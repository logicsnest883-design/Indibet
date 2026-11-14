from django.contrib import admin

from .models import Transaction, Wallet


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "available", "held", "updated_at")
    list_editable = ("available",)
    search_fields = ("user__username",)
    readonly_fields = ("user", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "wallet", "kind", "status", "amount", "reference")
    list_filter = ("kind", "status")
    search_fields = ("reference", "provider_ref", "wallet__user__username")