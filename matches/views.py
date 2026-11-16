from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from wallet.models import Wallet

from .models import Match

# Each game has its own board page
GAME_TEMPLATES = {
    "checkers": "checkers/board.html",
    "chess": "chessgame/board.html",
}


@login_required
def lobby(request):
    wallet, _ = Wallet.objects.get_or_create(user=request.user)
    context = {
        "wallet": wallet,
        "games": Match.Game.choices,
    }
    return render(request, "matches/lobby.html", context)


@login_required
def board(request, match_id):
    match = get_object_or_404(
        Match.objects.select_related("creator", "opponent"), pk=match_id
    )
    if request.user.id not in (match.creator_id, match.opponent_id):
        raise Http404
    if match.status not in (Match.Status.ACTIVE, Match.Status.FINISHED):
        return redirect("matches:lobby")

    template = GAME_TEMPLATES.get(match.game)
    if not template:
        raise Http404

    context = {"match": match, "pot": match.stake * 2}
    return render(request, template, context)