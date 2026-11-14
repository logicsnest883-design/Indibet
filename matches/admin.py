from django.contrib import admin

from wallet.services import refund_stake

from .models import Match


@admin.action(description="Cancel selected matches and refund stakes")
def cancel_and_refund(modeladmin, request, queryset):
    live = [Match.Status.OPEN, Match.Status.STARTING, Match.Status.ACTIVE]
    for match in queryset.filter(status__in=live):
        refund_stake(match.creator_id, match.stake, match.id, refund_fee=True)
        if match.opponent_id:
            refund_stake(match.opponent_id, match.stake, match.id, refund_fee=True)
        match.status = Match.Status.CANCELLED
        match.save(update_fields=["status"])


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ("id", "game", "creator", "opponent", "stake", "status", "created_at")
    list_filter = ("game", "status")
    actions = [cancel_and_refund]