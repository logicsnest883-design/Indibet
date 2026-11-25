"""
Knockout bracket builder (pure Python, no Django inside).

A bracket is a plain dict that is safe to store as JSON:

    {"size": 128, "entrants": 79, "rounds": [[match, match, ...], [...], ...]}

Every match is a dict:

    round     round number, starting at 1
    number    position inside the round, starting at 0
    p1, p2    a player id, BYE (an empty place: nobody will ever come), or None (still waiting
              for the result of an earlier match)
    winner    a player id, BYE, or None while undecided
    loser     a player id or None
    how       None, "played", "walkover", "bye" or "double_no_show"

Winners move forward by themselves: the winner of match N in a round goes to match N // 2 of the next
round (even N = first place, odd N = second place).

Rules this builder follows
- The bracket has the smallest power of two that fits everybody (79 players -> 128 places).
- The empty places are byes. Players are drawn at random, and the standard seeding layout spreads
  the byes out, so no match ever has two byes in round 1 and the top places can only meet late.
- A walkover (one player absent) sends the player who showed up through.
- If both players are absent, both are out and the place becomes a bye: their next opponent goes through.
"""
import copy
import random

BYE = "bye"  # an empty place in the bracket


class BracketError(ValueError):
    pass


def bracket_size(players):
    """Smallest power of two that fits this many players (at least 2)."""
    if players < 2:
        raise BracketError("A bracket needs at least 2 players.")
    size = 2
    while size < players:
        size *= 2
    return size


def seed_order(size):
    """Seed numbers in bracket order. Neighbours play each other in round 1 (1 v size, ...),
    and seeds 1 and 2 can only meet in the final."""
    order = [1]
    while len(order) < size:
        total = len(order) * 2 + 1
        new = []
        for seed in order:
            new += [seed, total - seed]
        order = new
    return order


def _new_match(round_number, number, p1, p2):
    return {
        "round": round_number,
        "number": number,
        "p1": p1,
        "p2": p2,
        "winner": None,
        "loser": None,
        "how": None,
    }


def _is_player(slot):
    return slot is not None and slot != BYE


def _advance(bracket, match):
    """Move the winner of a decided match into the next round."""
    rounds = bracket["rounds"]
    if match["round"] >= len(rounds):
        return  # the final has no next round
    nxt = rounds[match["round"]][match["number"] // 2]
    nxt["p1" if match["number"] % 2 == 0 else "p2"] = match["winner"]


def _settle(bracket):
    """Decide every match that has an empty place in it, and pass the winners forward."""
    for matches in bracket["rounds"]:
        for m in matches:
            if m["winner"] is not None:
                continue
            a, b = m["p1"], m["p2"]
            if a is None or b is None:
                continue  # still waiting for a result from the round before
            if a == BYE or b == BYE:
                m["winner"] = b if a == BYE else a
                m["how"] = "bye"
                _advance(bracket, m)


def build_bracket(player_ids, rng=None):
    """Draw the players into a bracket. Byes are settled straight away."""
    ids = list(player_ids)
    if len(ids) != len(set(ids)):
        raise BracketError("A player can only be in the bracket once.")
    size = bracket_size(len(ids))

    rng = rng or random.SystemRandom()
    rng.shuffle(ids)  # the draw: shuffled position = seed number

    seeded = ids + [BYE] * (size - len(ids))  # seeds beyond the last player are empty places
    order = seed_order(size)

    first_round = []
    for i in range(size // 2):
        a, b = order[2 * i], order[2 * i + 1]
        first_round.append(_new_match(1, i, seeded[a - 1], seeded[b - 1]))

    rounds = [first_round]
    count, number = size // 4, 2
    while count >= 1:
        rounds.append([_new_match(number, i, None, None) for i in range(count)])
        count //= 2
        number += 1

    bracket = {"size": size, "entrants": len(ids), "rounds": rounds}
    _settle(bracket)
    return bracket


# ---------- looking at a bracket ----------
def _find(bracket, round_number, number):
    if round_number < 1 or number < 0:
        raise BracketError("That match does not exist.")
    try:
        return bracket["rounds"][round_number - 1][number]
    except IndexError:
        raise BracketError("That match does not exist.")


def _require_ready(match):
    if match["winner"] is not None:
        raise BracketError("That match has already been decided.")
    if not (_is_player(match["p1"]) and _is_player(match["p2"])):
        raise BracketError("That match is not ready: it is waiting for its players.")


def ready_matches(bracket, round_number=None):
    """Matches where both players are known and nobody has won yet."""
    found = []
    for matches in bracket["rounds"]:
        for m in matches:
            if round_number is not None and m["round"] != round_number:
                continue
            if m["winner"] is None and _is_player(m["p1"]) and _is_player(m["p2"]):
                found.append(m)
    return found


def current_round(bracket):
    """The first round that still has an undecided match, or None when the bracket is finished."""
    for matches in bracket["rounds"]:
        if any(m["winner"] is None for m in matches):
            return matches[0]["round"]
    return None


def is_finished(bracket):
    return bracket["rounds"][-1][0]["winner"] is not None


def champion(bracket):
    winner = bracket["rounds"][-1][0]["winner"]
    return winner if _is_player(winner) else None


def placings(bracket):
    """Who finished where, for prizes.
    A player who lost only because they did not show up gets no placing."""
    rounds = bracket["rounds"]
    final = rounds[-1][0]
    result = {"champion": champion(bracket), "runner_up": None, "semifinalists": [], "quarterfinalists": []}

    if final["how"] == "played" and final["loser"] is not None:
        result["runner_up"] = final["loser"]

    for matches in rounds:
        key = {2: "semifinalists", 4: "quarterfinalists"}.get(len(matches))
        if not key:
            continue
        for m in matches:
            if m["how"] == "played" and m["loser"] is not None:
                result[key].append(m["loser"])
    return result


# ---------- recording results (each returns a new bracket and leaves the old one alone) ----------
def record_result(bracket, round_number, number, winner, how="played"):
    """The match was played ('played'), or one player did not show up ('walkover')."""
    if how not in ("played", "walkover"):
        raise BracketError("A result is either 'played' or 'walkover'.")
    new = copy.deepcopy(bracket)
    m = _find(new, round_number, number)
    _require_ready(m)
    if winner not in (m["p1"], m["p2"]):
        raise BracketError("The winner must be one of the two players.")

    m["winner"] = winner
    m["loser"] = m["p2"] if winner == m["p1"] else m["p1"]
    m["how"] = how
    _advance(new, m)
    _settle(new)
    return new


def record_double_no_show(bracket, round_number, number):
    """Neither player showed up. Both are out, and the place becomes a bye for the next opponent."""
    new = copy.deepcopy(bracket)
    m = _find(new, round_number, number)
    _require_ready(m)

    m["winner"] = BYE
    m["how"] = "double_no_show"
    _advance(new, m)
    _settle(new)
    return new


def describe(bracket, limit=12):
    """A short text summary, handy in the shell."""
    rounds = bracket["rounds"]
    first = rounds[0]
    byes = [m for m in first if BYE in (m["p1"], m["p2"])]
    real = [m for m in first if m not in byes]
    lines = [
        f"Bracket of {bracket['size']} for {bracket['entrants']} players: "
        f"{len(rounds)} rounds, {len(byes)} byes, {len(real)} real matches in round 1."
    ]
    for m in real[:limit]:
        lines.append(f"  Round 1, match {m['number'] + 1}: player {m['p1']} vs player {m['p2']}")
    if len(real) > limit:
        lines.append(f"  ... and {len(real) - limit} more real matches")
    return "\n".join(lines)