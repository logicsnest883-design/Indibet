from django.contrib import admin, messages
from django.db.models import Count
from django.utils.html import format_html

from .bracket import describe
from .models import Tournament, TournamentEntry, TournamentRound
from .services import TournamentError, cancel_tournament, start_tournament


@admin.action(description="Close registration, draw the bracket and start (ignores the closing time)")
def start_now(modeladmin, request, queryset):
    for t in queryset:
        try:
            outcome = start_tournament(t.pk, force=True)
        except TournamentError as e:
            modeladmin.message_user(request, f"{t.name}: {e}", messages.ERROR)
            continue
        if outcome == "cancelled":
            modeladmin.message_user(
                request, f"{t.name}: too few players. Cancelled and entry fees refunded.", messages.WARNING
            )
        else:
            modeladmin.message_user(request, f"{t.name}: bracket drawn and rounds scheduled.", messages.SUCCESS)


@admin.action(description="Cancel and refund entry fees")
def cancel_and_refund(modeladmin, request, queryset):
    for t in queryset:
        try:
            cancel_tournament(t.pk)
        except TournamentError as e:
            modeladmin.message_user(request, f"{t.name}: {e}", messages.ERROR)
            continue
        modeladmin.message_user(request, f"{t.name}: cancelled and entry fees refunded.", messages.SUCCESS)


class TournamentRoundInline(admin.TabularInline):
    model = TournamentRound
    extra = 0
    fields = ("number", "starts_at", "started_at")
    readonly_fields = ("number", "started_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "game",
        "status",
        "starts_at",
        "enrolled",
        "prize_mode",
        "entry_fee",
        "match_fee",
        "required_referrals",
    )
    list_filter = ("status", "game", "prize_mode")
    list_editable = ("status",)
    search_fields = ("name",)
    actions = [start_now, cancel_and_refund]
    inlines = [TournamentRoundInline]
    readonly_fields = ("prize_preview", "draw_summary", "final_players", "final_prize_pool", "drawn_at")

    fieldsets = (
        ("Basics", {"fields": ("name", "description", "game", "format", "status")}),
        ("Players", {"fields": ("min_players", "max_players")}),
        (
            "Timing (Lusaka time)",
            {
                "fields": (
                    "registration_closes_at",
                    "starts_at",
                    "round_interval_minutes",
                    "daily_open",
                    "daily_close",
                    "checkin_minutes",
                )
            },
        ),
        ("Costs", {"fields": ("entry_fee", "match_fee")}),
        ("Referrals", {"fields": ("required_referrals", "waive_referrals_at_users")}),
        (
            "Prizes",
            {
                "fields": (
                    "prize_mode",
                    "fixed_prize_pool",
                    "platform_cut_percent",
                    "prize_per_player",
                    "prize_cap",
                    "share_champion",
                    "share_runner_up",
                    "share_semifinalist",
                    "share_quarterfinalist",
                    "prize_preview",
                )
            },
        ),
        ("The draw (filled in when the tournament starts)", {"fields": ("draw_summary", "final_players", "final_prize_pool", "drawn_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_enrolled=Count("entries"))

    @admin.display(description="Enrolled", ordering="_enrolled")
    def enrolled(self, obj):
        return obj._enrolled

    @admin.display(description="Prize preview")
    def prize_preview(self, obj):
        if not obj.pk:
            return "Save the tournament first to see a preview."
        if obj.prize_mode == Tournament.PrizeMode.FIXED:
            return (
                f"Fixed prize pool: K{obj.prize_pool(0):.2f}, whatever the number of players. "
                "Entry fees (if any) are all kept by IndiBet."
            )
        lines = []
        for n in (8, 16, 32, 64, 128):
            b = obj.prize_breakdown(n)
            lines.append(
                f"{n} players: prize pool K{b['pool']:.2f} "
                f"(entry fees K{b['entry_fees']:.2f}, IndiBet keeps K{b['platform_keeps']:.2f}, "
                f"IndiBet adds K{b['bonus']:.2f})"
            )
        return " | ".join(lines)

    @admin.display(description="Bracket")
    def draw_summary(self, obj):
        if not obj.bracket:
            return "Not drawn yet. Use the action 'Close registration, draw the bracket and start'."
        return format_html("<pre style='margin:0'>{}</pre>", describe(obj.bracket))


@admin.register(TournamentEntry)
class TournamentEntryAdmin(admin.ModelAdmin):
    list_display = ("tournament", "user", "fee_paid", "enrolled_at")
    list_filter = ("tournament",)
    search_fields = ("user__username", "user__phone")

    # Entries are created and removed only through the site, so entry fees are always handled properly.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False