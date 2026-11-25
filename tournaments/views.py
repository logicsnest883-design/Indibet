from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from matches.models import Match
from wallet.services import InsufficientFunds

from .bracket_data import bracket_json
from .checkin import round_label, waiting_notification
from .models import Tournament
from .schedule import round_name
from .services import EnrolmentError, enrol, withdraw


def tournament_list(request):
    total_users = get_user_model().objects.count()
    tournaments = list(
        Tournament.objects.exclude(status=Tournament.Status.DRAFT).annotate(enrolled=Count("entries"))
    )
    for t in tournaments:
        t.pool_now = t.current_prize_pool(t.enrolled)
        t.refs_needed = t.referrals_needed(total_users)

    def pick(*statuses):
        return [t for t in tournaments if t.status in statuses]

    sections = [
        ("Open for registration", pick(Tournament.Status.REGISTRATION)),
        ("Running now", pick(Tournament.Status.RUNNING)),
        ("Finished", pick(Tournament.Status.FINISHED, Tournament.Status.CANCELLED)),
    ]
    return render(request, "tournaments/list.html", {"sections": sections})


def tournament_detail(request, pk):
    t = get_object_or_404(Tournament.objects.exclude(status=Tournament.Status.DRAFT), pk=pk)
    enrolled = t.entries.count()
    needed = t.referrals_needed(get_user_model().objects.count())

    entry = None
    have = 0
    if request.user.is_authenticated:
        entry = t.entries.filter(user=request.user).first()
        have = request.user.referrals.count()

    schedule = []
    if t.bracket:
        for r in t.rounds.all():
            matches_in_round = len(t.bracket["rounds"][r.number - 1])
            schedule.append(
                {
                    "label": round_name(matches_in_round),
                    "starts_at": r.starts_at,
                    "started": r.started_at is not None,
                }
            )

    context = {
        "t": t,
        "enrolled": enrolled,
        "spots_left": max(0, t.max_players - enrolled),
        "full": enrolled >= t.max_players,
        "open": t.registration_is_open(),
        "pool": t.current_prize_pool(enrolled),
        "prizes": t.prize_table(enrolled),
        "needed": needed,
        "have": have,
        "missing": max(0, needed - have),
        "entry": entry,
        "schedule": schedule,
        "players": t.entries.select_related("user")[:200],
    }
    return render(request, "tournaments/detail.html", context)


def bracket_page(request, pk):
    t = get_object_or_404(Tournament.objects.exclude(status=Tournament.Status.DRAFT), pk=pk)
    if not t.bracket:
        return redirect("tournaments:detail", pk=pk)  # nothing to show before the draw
    return render(request, "tournaments/bracket.html", {"t": t})


def bracket_data(request, pk):
    """The bracket as data. The bracket page asks for this every few seconds."""
    t = get_object_or_404(Tournament.objects.exclude(status=Tournament.Status.DRAFT), pk=pk)
    if not t.bracket:
        raise Http404
    return JsonResponse(bracket_json(t))


@login_required
@require_POST
def enrol_view(request, pk):
    try:
        enrol(request.user, pk)
        messages.success(request, "You are enrolled. Good luck!")
    except InsufficientFunds:
        messages.error(request, "You don't have enough balance for the entry fee.")
    except EnrolmentError as e:
        messages.error(request, str(e))
    return redirect("tournaments:detail", pk=pk)


@login_required
@require_POST
def withdraw_view(request, pk):
    try:
        withdraw(request.user, pk)
        messages.success(request, "You have left the tournament.")
    except EnrolmentError as e:
        messages.error(request, str(e))
    return redirect("tournaments:detail", pk=pk)


@login_required
def match_room(request, match_id):
    """The check-in room of a tournament match."""
    match = get_object_or_404(
        Match.objects.select_related("tournament", "creator", "opponent"),
        pk=match_id,
        tournament__isnull=False,
    )
    if request.user.id not in (match.creator_id, match.opponent_id):
        raise Http404
    if match.status == Match.Status.ACTIVE:
        return redirect("matches:board", match_id=match.id)

    opponent = match.opponent if request.user.id == match.creator_id else match.creator
    context = {"match": match, "opponent": opponent, "round_label": round_label(match)}
    return render(request, "tournaments/match_room.html", context)


@login_required
def my_match(request):
    """Used by the banner on every page: is a tournament match waiting for this player?"""
    return JsonResponse({"notify": waiting_notification(request.user.id)})