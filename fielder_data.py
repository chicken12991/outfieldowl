import numpy as np
from pybaseball import statcast_outfielder_jump, statcast_sprint_speed

# league-average fallback for anyone missing from the leaderboards.
# ~32 ft bootup on 2-star plays, 27 ft/s sprint.
DEFAULT_ABILITY = {'bootup_feet': 32.0, 'sprint_speed': 27.0}

# ---------------------------------------------------------
# RUNNER-TIME MODEL (contact -> base, from the batter's own numbers)
# hp_to_1b covers the launch out of the box (follow-through +
# acceleration over the first 90 ft); after that the runner is at
# speed and covers the remaining base path at sprint_speed.
# ---------------------------------------------------------

EXTRA_DIST_2B = 105.0    # ft past first base, incl. the rounding arc
EXTRA_DIST_3B = 215.0    # ft past first base, incl. two rounding arcs
HP1B_FALLBACK_PAD = 1.05 # est. hp_to_1b = 90/sprint + this, seconds
DEFAULT_RUNNER = {'sprint_speed': 27.0, 'hp_to_1b': 4.4}


def load_fielder_ability(names, year=2026):
    """pull statcast jump + sprint speed for the given player names.
    returns {name: {'bootup_feet', 'sprint_speed'}}.
      bootup_feet  = f_bootup_distance: total feet covered in the
                     first 3 seconds on 2-star+ plays (absolute)
      sprint_speed = ft/s, used after the 3-second bootup window
    anyone not found on a leaderboard falls back to league average
    for that number (and prints a note so you know)."""

    jump = statcast_outfielder_jump(year)
    speed = statcast_sprint_speed(year, 0)   # 0 = no min opportunities

    ability = {}
    for name in names:
        parts = name.split()
        savant_name = f"{parts[-1]}, {parts[0]}"   # "Wood, James"

        entry = dict(DEFAULT_ABILITY)

        jrow = jump[jump['last_name, first_name'] == savant_name]
        if not jrow.empty:
            entry['bootup_feet'] = float(jrow['f_bootup_distance'].iloc[0])
        else:
            print(f"note: {name} not on jump leaderboard, "
                  f"using league-average bootup")

        srow = speed[speed['last_name, first_name'] == savant_name]
        if not srow.empty:
            entry['sprint_speed'] = float(srow['sprint_speed'].iloc[0])
        else:
            print(f"note: {name} not on sprint speed leaderboard, "
                  f"using league average")

        ability[name] = entry

    return ability


def load_runner_times(last_name, first_name, year=2026):
    """runner times (contact -> 2B, contact -> 3B) for one batter,
    built from THEIR statcast sprint_speed + hp_to_1b.
    returns (t_2b, t_3b) in seconds.
    fallbacks, in order:
      - hp_to_1b missing -> estimated as 90/sprint + pad
      - batter not on the leaderboard at all -> league-average runner
    matching is case-insensitive so lowercase lineup names work."""

    speed = statcast_sprint_speed(year, 0)

    savant_name = f"{last_name}, {first_name}".lower()
    row = speed[speed['last_name, first_name'].str.lower() == savant_name]

    sprint = DEFAULT_RUNNER['sprint_speed']
    hp1b = DEFAULT_RUNNER['hp_to_1b']

    if not row.empty:
        s = row['sprint_speed'].iloc[0]
        if np.isfinite(s):
            sprint = float(s)
        h = row['hp_to_1b'].iloc[0]
        if np.isfinite(h):
            hp1b = float(h)
        else:
            hp1b = 90.0 / sprint + HP1B_FALLBACK_PAD
            print(f"note: no hp_to_1b for {first_name} {last_name}, "
                  f"estimating {hp1b:.2f}s from sprint speed")
    else:
        print(f"note: {first_name} {last_name} not on sprint speed "
              f"leaderboard, using league-average runner")

    t_2b = hp1b + EXTRA_DIST_2B / sprint
    t_3b = hp1b + EXTRA_DIST_3B / sprint

    print(f"runner model for {first_name} {last_name}: "
          f"sprint {sprint:.1f} ft/s, hp->1B {hp1b:.2f}s "
          f"-> 2B {t_2b:.2f}s, 3B {t_3b:.2f}s")
    return t_2b, t_3b