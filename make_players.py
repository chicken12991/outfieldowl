"""
step 4: build the list of every player in the MLB season, for the
website's search bar. run from the beisbol folder:
    python make_players.py
writes docs/players.json
"""
import json
import os

import requests

from fielder_data import plain_name

SEASON = 2026
API = "https://statsapi.mlb.com/api/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'docs', 'players.json')

# MLB + the four full-season minor league levels (AAA, AA, A+, A),
# so a player who got sent down still shows his MLB club
TEAM_SPORT_IDS = "1,11,12,13,14"


def get(path, **params):
    """one request to MLB's stats API, returned as a python dict."""
    r = requests.get(f"{API}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def team_abbreviations():
    """team id -> MLB club abbreviation ('WSH'). minor league teams
    map to their parent club."""
    teams = get("teams", sportIds=TEAM_SPORT_IDS, season=SEASON)["teams"]
    mlb = {t["id"]: t.get("abbreviation", "") for t in teams
           if t.get("sport", {}).get("id") == 1}
    out = dict(mlb)
    for t in teams:
        parent = t.get("parentOrgId")
        if t["id"] not in out and parent in mlb:
            out[t["id"]] = mlb[parent]
    return out


def player_type(pos):
    """'pitcher', 'two-way' (Ohtani) or 'batter'."""
    if pos == "P":
        return "pitcher"
    if pos == "TWP":
        return "two-way"
    return "batter"


def main():
    abbr = team_abbreviations()
    people = get("sports/1/players", season=SEASON)["people"]

    # so we can see what MLB actually gives us
    print("fields on the first player:", sorted(people[0].keys()))

    players = []
    for p in people:
        pos = p.get("primaryPosition", {}).get("abbreviation", "")
        team_id = p.get("currentTeam", {}).get("id")
        players.append({
            "id": p["id"],
            "name": p.get("fullName", ""),
            "first": p.get("useName") or p.get("firstName", ""),
            "last": p.get("lastName", ""),
            "team": abbr.get(team_id, ""),
            "pos": pos,
            "type": player_type(pos),
        })
    players.sort(key=lambda x: (plain_name(x["last"]),
                                plain_name(x["first"])))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(players, f, ensure_ascii=False, separators=(',', ':'))

    # ---- summary ----
    by_type = {}
    for p in players:
        by_type[p["type"]] = by_type.get(p["type"], 0) + 1
    no_team = sum(1 for p in players if not p["team"])
    print(f"\n{len(players)} players: {by_type}")
    print(f"players with no team found: {no_team}")
    print(f"wrote {OUT} ({os.path.getsize(OUT):,} bytes)")

    # ---- spot checks ----
    print("\nspot checks:")
    for check in ("Arraez", "Realmuto", "Ohtani", "Schwarber"):
        hits = [p for p in players
                if plain_name(p["last"]).startswith(plain_name(check))]
        for p in hits:
            print("  ", json.dumps(p, ensure_ascii=False))
        if not hits:
            print(f"   (no match for {check})")


if __name__ == '__main__':
    main()