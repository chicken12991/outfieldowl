import os
import numpy as np
import pandas as pd
from batted_balls import get_player_id
from fielders import DEFAULT_INFIELD
from fielder_data import load_fielder_ability, load_runner_times
from optimizer import set_park, current_park, \
    MODE_MIN_RUNS, MODE_PREVENT_HIT
from engine import prepare_batter, analyze_prepared
from field_plot import plot_alignment

pd.set_option('display.max_rows', None)
pd.set_option('display.width', None)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------

LINEUP_FILE = os.path.join(SCRIPT_DIR, 'lineup.txt')
PLOTS_DIR = os.path.join(SCRIPT_DIR, 'plots')
os.makedirs(PLOTS_DIR, exist_ok=True)
START_DT, END_DT = "2026-03-26", "2026-07-11"

# park for command-line runs (team key from mlbstadiums.csv, e.g.
# 'nationals', 'red_sox'); the GUI passes its own pick
PARK = 'nationals'

# default outfield for command-line runs; the GUI passes its own picks
PLAYERS = ['Andrew Pinckney', 'Jacob Young', 'Dylan Crews']
OPTIMIZE_MODE = 'corners'   # 'corners' or 'all'
CENTER_PLAYER = 'Jacob Young' if OPTIMIZE_MODE == 'corners' else None

IMAGES_DIR = os.path.join(SCRIPT_DIR, 'images')

# the full roster of selectable outfielders (the GUI lists these)
PLAYER_IMAGES_BY_NAME = {
    'James Wood':  os.path.join(IMAGES_DIR, 'jameswood.png'),
    'Jacob Young': os.path.join(IMAGES_DIR, 'jacobyoung.png'),
    'Dylan Crews': os.path.join(IMAGES_DIR, 'dylancrews.png'),
    'Daylen Lile': os.path.join(IMAGES_DIR, 'daylenlile.png'),
    'Andrew Pinckney': os.path.join(IMAGES_DIR, 'andrewPinckney.jpg'),
}

# ---------------------------------------------------------
# SCORING MODES
# ---------------------------------------------------------

MODE_LABELS = {
    MODE_MIN_RUNS: "Minimize expected runs allowed",
    MODE_PREVENT_HIT: "Prevent a hit at all costs (ignores what a miss "
                      "turns into)",
}

MODE_SCORE_LABELS = {
    MODE_MIN_RUNS: "expected runs allowed",
    MODE_PREVENT_HIT: "expected hits allowed",
}

MODE_FILE_SUFFIX = {
    MODE_MIN_RUNS: "minruns",
    MODE_PREVENT_HIT: "preventhit",
}


def prompt_for_mode():
    """keyboard entry point for choosing a mode (CLI only - the GUI
    passes a MODE_* constant straight into run/analyze_batter)."""
    print("\nchoose an optimization mode:")
    print(f"  1) {MODE_LABELS[MODE_MIN_RUNS]}")
    print(f"  2) {MODE_LABELS[MODE_PREVENT_HIT]}")
    choice = input("mode [1/2]: ").strip()
    return MODE_PREVENT_HIT if choice == '2' else MODE_MIN_RUNS


def analyze_batter(last_name, first_name, mode, abilities,
                   players=None, center_player=None):
    """one batter = one full analysis + saved plot, scored under 'mode',
    in whatever park optimizer.set_park() last chose.
    calls the two halves in engine.py:
      prepare_batter   -> savant pull + hang-time physics
      analyze_prepared -> homers/infield outs for this park + optimizer
    players / center_player default to the module-level config so the
    CLI keeps working; the GUI passes its own selections.
    returns the saved plot's path (or None if skipped)."""
    if players is None:
        players = PLAYERS
    if center_player is None and OPTIMIZE_MODE == 'corners' \
            and CENTER_PLAYER in players:
        center_player = CENTER_PLAYER

    # ---- half 1: savant + physics (no choices involved) ----
    batter_id = get_player_id(last_name, first_name)
    air = prepare_batter(batter_id, START_DT, END_DT)

    if len(air) == 0:
        print(f"no air balls found for {first_name} {last_name}, skipping")
        return None

    runner_times = load_runner_times(batter_id, year=2026,
                                     label=f"{first_name} {last_name}")

    # ---- half 2: park + fielders + mode ----
    result = analyze_prepared(air, players, abilities,
                              park=current_park()[0], mode=mode,
                              runner_times=runner_times,
                              center_player=center_player)

    best_fielders = result['fielders']
    assignment = result['assignment']
    score = result['score']
    all_balls = result['balls']
    park_key, park_name = result['park_key'], result['park_name']

    score_label = MODE_SCORE_LABELS[mode]
    print(f"\nballs the outfield had to handle: {result['n_of_balls']} "
          f"({park_name})")
    print(f"--- {first_name.title()} {last_name.title()}: "
          f"optimal alignment ({MODE_LABELS[mode]}, {park_name}) ---")
    for pos, (x, y) in best_fielders.items():
        print(f"{pos} ({assignment[pos]}): x={x:.0f} ft, y={y:.0f} ft "
              f"({np.hypot(x, y):.0f} ft from home)")
    print(f"{score_label}: {score:.2f} over {result['n_of_balls']} balls")

    # plot, saved to plots/, no popup window
    player_names = {pos: assignment[pos] for pos in best_fielders}
    player_images = {pos: PLAYER_IMAGES_BY_NAME.get(assignment[pos])
                     for pos in best_fielders}
    suffix = MODE_FILE_SUFFIX[mode]
    # keep only safe characters in the name part, then add the extension
    stem = f"{first_name}{last_name}_{park_key}_{suffix}_plot"
    stem = ''.join(c for c in stem if c.isalnum() or c == '_')
    save_path = os.path.join(PLOTS_DIR, stem + ".png")

    plot_alignment(all_balls, best_fielders,
                   title=f"{first_name.title()} {last_name.title()} "
                         f"air balls, 2026 season "
                         f"({len(all_balls)} balls) - {park_name}\n"
                         f"{MODE_LABELS[mode]} - "
                         f"{score_label}: {score:.1f}",
                   infield=DEFAULT_INFIELD,
                   player_images=player_images,
                   player_names=player_names,
                   save_path=save_path,
                   show=False)
    return save_path


def run(mode):
    """run the whole lineup under the given scoring mode (CLI path)."""
    set_park(PARK)
    with open(LINEUP_FILE, encoding='utf-8-sig') as f:
        lineup = [line.split() for line in f if line.strip()]
    print(f"lineup: {len(lineup)} batters")
    print(f"mode: {MODE_LABELS[mode]}")
    print(f"park: {current_park()[1]}")

    abilities = load_fielder_ability(PLAYERS, year=2026)
    print("\n--- fielder ability (statcast) ---")
    for name, ab in abilities.items():
        print(f"{name}: bootup {ab['bootup_feet']:.1f} ft / 3s, "
              f"sprint {ab['sprint_speed']:.1f} ft/s")

    for first_name, last_name in lineup:
        print(f"\n{'='*55}\n{first_name.title()} {last_name.title()}"
              f"\n{'='*55}")
        try:
            analyze_batter(last_name, first_name, mode, abilities,
                           players=PLAYERS, center_player=CENTER_PLAYER)
        except Exception as e:
            print(f"FAILED for {first_name} {last_name}: {e}")
            print(f"  name repr: {first_name!r} {last_name!r}")
            continue

    print("\ndone - plots saved in", PLOTS_DIR)


if __name__ == '__main__':
    run(prompt_for_mode())