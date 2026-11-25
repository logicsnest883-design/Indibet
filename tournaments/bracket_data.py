"""The bracket as plain data for the bracket page (with player names instead of ids)."""
from django.contrib.auth import get_user_model

from .bracket import BYE, champion
from .schedule import round_name


def _player_ids(bracket):
    found = set()
    for matches in bracket["rounds"]:
        for m in matches:
            for slot in (m["p1"], m["p2"], m["winner"]):
                if isinstance(slot, int):
                    found.add(slot)
    return found


def bracket_json(tournament):
    bracket = tournament.bracket
    names = dict(
        get_user_model().objects.filter(pk__in=_player_ids(bracket)).values_list("id", "username")
    )

    def who(slot):
        if slot is None:
            return None  # still waiting for the result of an earlier match
        if slot == BYE:
            return "bye"
        return {"id": slot, "name": names.get(slot, "?")}

    rounds = []
    for number, matches in enumerate(bracket["rounds"], start=1):
        rounds.append(
            {
                "number": number,
                "label": round_name(len(matches)),
                "matches": [
                    {
                        "number": m["number"],
                        "p1": who(m["p1"]),
                        "p2": who(m["p2"]),
                        "winner": m["winner"] if isinstance(m["winner"], int) else None,
                        "how": m["how"],
                    }
                    for m in matches
                ],
            }
        )

    champ = champion(bracket)
    return {
        "status": tournament.status,
        "rounds": rounds,
        "champion": names.get(champ) if champ else None,
    }