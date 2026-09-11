import numpy as np

# ---------------------------------------------------------
# FIELDER POSITIONS
# ---------------------------------------------------------

# a 'default average' outfield alignment, in feet from home plate
DEFAULT_OUTFIELD = {
    'LF': (-112, 262),
    'CF': (0, 320),
    'RF': (112, 262),
}

# standard infield alignment, feet from home plate.
# these are FIXED - the optimizer never moves them.
DEFAULT_INFIELD = {
    '3B': (-72, 98),    # shallow, hugging the line near the bag
    'SS': (-58, 148),   # deep, in the 3B-2B hole
    '2B': (62, 138),    # deep, in the 1B-2B hole
    '1B': (65, 90),     # shallow, hugging the line near the bag
}

# league-average ability (used for infielders and any fielder
# without specific statcast data)
LEAGUE_AVG_ABILITY = {'bootup_feet': 32.0, 'sprint_speed': 27.0}


# ---------------------------------------------------------
# REACHABILITY / CATCH PROBABILITY (two-phase model)
# ---------------------------------------------------------

def reachable_distance(hang_time, bootup_feet, sprint_speed):
    """feet a fielder can cover in hang_time seconds:
      0-3s : their statcast bootup distance (pro-rated if less time)
      3s+  : sprint speed for the remaining time
    works on scalars or numpy arrays of hang_time."""
    hang_time = np.asarray(hang_time, dtype=float)
    partial = bootup_feet * (hang_time / 3.0)
    full = bootup_feet + sprint_speed * (hang_time - 3.0)
    return np.where(hang_time <= 3.0, partial, full)

def time_to_cover(dist, bootup_feet, sprint_speed):
    """seconds for a fielder to cover 'dist' feet: the inverse of
    reachable_distance. within the bootup envelope, time is pro-rated
    over the 3-second window; beyond it, sprint speed takes over."""
    dist = np.asarray(dist, dtype=float)
    within = 3.0 * dist / bootup_feet
    beyond = 3.0 + (dist - bootup_feet) / sprint_speed
    return np.where(dist <= bootup_feet, within, beyond)

def catch_prob_from_reach(dist, reach):
    """bucket catch probability (100/75/50/25/0) by how comfortably
    the fielder covers the distance. works on scalars or arrays."""
    dist = np.asarray(dist, dtype=float)
    reach = np.asarray(reach, dtype=float)
    ratio = np.divide(dist, reach, out=np.full_like(dist, np.inf),
                      where=reach > 0)
    return np.select(
        [ratio <= 0.70, ratio <= 0.85, ratio <= 0.95, ratio <= 1.05],
        [100, 75, 50, 25],
        default=0
    )


def catch_probability(fielder_pos, ball_x, ball_y, hang_time,
                      ability=None):
    """catch probability for ONE fielder on ONE ball, using their
    statcast ability (bootup + sprint). league average if none given."""
    if ability is None:
        ability = LEAGUE_AVG_ABILITY
    dist = np.hypot(ball_x - fielder_pos[0], ball_y - fielder_pos[1])
    reach = reachable_distance(hang_time, ability['bootup_feet'],
                               ability['sprint_speed'])
    return int(catch_prob_from_reach(dist, reach))


def best_catch_chance(ball_x, ball_y, hang_time, fielders=None,
                      abilities=None):
    """check every fielder on a ball, return (best_probability, who).
    'abilities' maps position -> ability dict; missing = league avg."""
    if fielders is None:
        fielders = DEFAULT_OUTFIELD
    abilities = abilities or {}

    best_prob, best_name = 0, None
    for name, pos in fielders.items():
        p = catch_probability(pos, ball_x, ball_y, hang_time,
                              abilities.get(name))
        if p > best_prob:
            best_prob, best_name = p, name
    return best_prob, best_name


def add_catch_columns(air, fielders=None, abilities=None):
    """add catch probability columns to an air-ball dataframe.
    expects landing_x / landing_y / hang_time to already exist."""
    results = air.apply(
        lambda row: best_catch_chance(row['landing_x'], row['landing_y'],
                                      row['hang_time'], fielders,
                                      abilities),
        axis=1
    )
    air['catch_prob'] = [r[0] for r in results]
    air['nearest_fielder'] = [r[1] for r in results]
    return air