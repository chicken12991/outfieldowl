"""


request (JSON):
  {
    "player_id": 656941,
    "name": "Kyle Schwarber",          (optional, used in the plot title)
    "fielders": ["Andrew Pinckney", "Jacob Young", "Dylan Crews"],
    "park": "nationals",               (optional, default nationals)
    "mode": "min_runs"                 (or "prevent_hit"; default min_runs)
  }

response (JSON): the plot as base64 text + the key numbers.
works both when called directly (local testing) and through a
Lambda Function URL (the website), which wraps the request in 'body'.
"""
import base64
import json
import os
import time
import traceback

# on AWS only /tmp is writable, so point cache folders there BEFORE
# matplotlib loads. (LAMBDA_TASK_ROOT only exists on Lambda, so this
# does nothing on your laptop.)
if os.environ.get("LAMBDA_TASK_ROOT"):
    os.environ["HOME"] = "/tmp"
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib
matplotlib.use("Agg")

import numpy as np

from engine import prepare_batter, analyze_prepared
from fielder_data import load_fielder_ability, load_runner_times
from fielders import DEFAULT_INFIELD
from field_plot import plot_alignment
from optimizer import list_parks, MODE_MIN_RUNS, MODE_PREVENT_HIT

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(HERE, "images")

# keep these in sync with run_analysis.py
SEASON = 2026
START_DT, END_DT = "2026-03-26", "2026-07-11"
DEFAULT_PARK = "nationals"
FIXED_CENTER = "Jacob Young"   # fixed CF when picked ('corners' mode)

# the outfielders a request is allowed to pick, with their headshots
ROSTER_IMAGES = {
    "James Wood":      os.path.join(IMAGES_DIR, "jameswood.png"),
    "Jacob Young":     os.path.join(IMAGES_DIR, "jacobyoung.png"),
    "Dylan Crews":     os.path.join(IMAGES_DIR, "dylancrews.png"),
    "Daylen Lile":     os.path.join(IMAGES_DIR, "daylenlile.png"),
    "Andrew Pinckney": os.path.join(IMAGES_DIR, "andrewPinckney.jpg"),
}

MODE_LABELS = {
    MODE_MIN_RUNS: "Minimize expected runs allowed",
    MODE_PREVENT_HIT: "Prevent a hit at all costs (ignores what a miss "
                      "turns into)",
}

MODE_SCORE_LABELS = {
    MODE_MIN_RUNS: "expected runs allowed",
    MODE_PREVENT_HIT: "expected hits allowed",
}


class BadRequest(ValueError):
    """the request itself is wrong (missing or invalid choices)."""


# ---------------------------------------------------------
# REQUEST / RESPONSE HELPERS
# ---------------------------------------------------------

def _read_request(event):
    """get the request dict. called directly, the event IS the request;
    through a Function URL, the request is a JSON string in 'body'."""
    if isinstance(event, dict) and "body" in event:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        return json.loads(body)
    return event or {}


def _reply(status, payload):
    """a response in the shape Function URLs expect."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def _validate(req):
    """check every choice; returns (player_id, name, fielders, park, mode)."""
    try:
        player_id = int(req.get("player_id"))
    except (TypeError, ValueError):
        raise BadRequest("player_id must be a number")

    fielders = req.get("fielders") or []
    if len(fielders) != 3 or len(set(fielders)) != 3:
        raise BadRequest("pick exactly 3 different outfielders")
    unknown = [f for f in fielders if f not in ROSTER_IMAGES]
    if unknown:
        raise BadRequest(f"unknown outfielder(s): {', '.join(unknown)}")

    park = req.get("park") or DEFAULT_PARK
    if park not in list_parks().values():
        raise BadRequest(f"unknown park: {park}")

    mode = req.get("mode") or MODE_MIN_RUNS
    if mode not in MODE_LABELS:
        raise BadRequest(f"unknown mode: {mode}")

    name = (req.get("name") or f"Player {player_id}").strip()
    return player_id, name, fielders, park, mode


# ---------------------------------------------------------
# THE FRONT DOOR
# ---------------------------------------------------------

def lambda_handler(event, context):
    start = time.time()

    # ---- 1. read + check the request ----
    try:
        player_id, name, fielders, park, mode = \
            _validate(_read_request(event))
    except BadRequest as e:
        return _reply(400, {"error": str(e)})
    except json.JSONDecodeError:
        return _reply(400, {"error": "request body isn't valid JSON"})

    try:
        # ---- 2. half 1: savant + physics ----
        air = prepare_batter(player_id, START_DT, END_DT)
        if len(air) == 0:
            return _reply(404, {"error": f"no air balls found for {name} "
                                         f"between {START_DT} and {END_DT}"})

        # ---- 3. half 2: park + fielders + mode ----
        abilities = load_fielder_ability(fielders, year=SEASON)
        runner_times = load_runner_times(player_id, year=SEASON, label=name)
        center = FIXED_CENTER if FIXED_CENTER in fielders else None
        result = analyze_prepared(air, fielders, abilities, park=park,
                                  mode=mode, runner_times=runner_times,
                                  center_player=center)

        # ---- 4. the plot, as png bytes in memory ----
        assignment = result["assignment"]
        score = result["score"]
        balls = result["balls"]
        score_label = MODE_SCORE_LABELS[mode]
        png = plot_alignment(
            balls, result["fielders"],
            title=f"{name} air balls, {SEASON} season "
                  f"({len(balls)} balls) - {result['park_name']}\n"
                  f"{MODE_LABELS[mode]} - {score_label}: {score:.1f}",
            infield=DEFAULT_INFIELD,
            player_names={p: assignment[p] for p in result["fielders"]},
            player_images={p: ROSTER_IMAGES.get(assignment[p])
                           for p in result["fielders"]},
            show=False, return_png=True)

        # ---- 5. package the answer ----
        alignment = [
            {"pos": pos, "player": assignment[pos],
             "x": round(float(x)), "y": round(float(y)),
             "dist": round(float(np.hypot(x, y)))}
            for pos, (x, y) in result["fielders"].items()
        ]
        return _reply(200, {
            "player_id": player_id,
            "name": name,
            "park": park,
            "park_name": result["park_name"],
            "mode": mode,
            "score": round(float(score), 2),
            "score_label": score_label,
            "n_balls": len(balls),
            "n_of_balls": result["n_of_balls"],
            "alignment": alignment,
            "image_png_base64": base64.b64encode(png).decode("ascii"),
            "seconds": round(time.time() - start, 1),
        })

    except Exception as e:
        traceback.print_exc()   # goes to the logs on AWS
        return _reply(500, {"error": f"analysis failed: {e}"})