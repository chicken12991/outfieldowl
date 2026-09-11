import numpy as np
from itertools import permutations
from tqdm import tqdm

from fielders import reachable_distance, catch_prob_from_reach, \
    time_to_cover, LEAGUE_AVG_ABILITY

# ---------------------------------------------------------
# TUNABLE SETTINGS
# ---------------------------------------------------------

GRID_SPACING = 10.0     # feet between candidate positions <- change me
OUTFIELD_START = 160.0  # distance from home where the grass starts

# run values per hit type (linear weights)
DAMAGE = {'single': 0.47, 'double': 0.77, 'triple': 1.05}

# ground/roll model: what happens after the ball lands
ROLL_RETENTION = 0.55   # fraction of horizontal speed kept after the bounce
ROLL_FRICTION = 12.0    # deceleration on grass, ft/s^2

# the race model: defense vs a FAST (90th percentile) baserunner
THROW_SPEED = 130.0     # outfield throw, ft/s (~88 mph)
TRANSFER_TIME = 0.9     # seconds: pickup -> ball leaves the hand
RUNNER_2B_TIME = 7.9    # fast runner, contact -> second base, seconds
RUNNER_3B_TIME = 11.4   # fast runner, contact -> third base, seconds

# minimum catch prob for a FIXED infielder to count as recording an out
INFIELD_OUT_THRESHOLD = 75

# ---------------------------------------------------------
# SCORING MODES
# ---------------------------------------------------------
# MODE_MIN_RUNS    : the original model - weighs a missed ball by what it
#                     turns into (single/double/triple), via the race model.
# MODE_PREVENT_HIT : cares only about catch vs. no-catch. every uncaught
#                     ball counts the same regardless of the race model,
#                     since the whole point is to never let it get that far.
MODE_MIN_RUNS = 'min_runs'
MODE_PREVENT_HIT = 'prevent_hit'


# ---------------------------------------------------------
# PARK DIMENSIONS - Nationals Park
# ---------------------------------------------------------

def _polar_to_xy(angle_deg, dist):
    a = np.radians(angle_deg)
    return dist * np.sin(a), dist * np.cos(a)

_WALL_CORNERS = [
    (-45.0, 337.0),   # LF foul pole
    (-22.5, 377.0),   # left-center corner
    (  0.0, 402.0),   # dead center corner
    ( 22.5, 370.0),   # right-center corner
    ( 45.0, 335.0),   # RF foul pole
]

_WALL_XY = np.array([_polar_to_xy(a, d) for a, d in _WALL_CORNERS])


def _ray_cast(angle_deg):
    """distance from home plate to the wall polyline along one ray."""
    dx, dy = np.sin(np.radians(angle_deg)), np.cos(np.radians(angle_deg))
    best = np.inf
    for (x1, y1), (x2, y2) in zip(_WALL_XY[:-1], _WALL_XY[1:]):
        sx, sy = x2 - x1, y2 - y1
        denom = dx * sy - dy * sx
        if abs(denom) < 1e-12:
            continue
        t = (x1 * sy - y1 * sx) / denom
        s = (x1 * dy - y1 * dx) / denom
        if t > 0 and -1e-6 <= s <= 1 + 1e-6:
            best = min(best, t)
    return best


_LOOKUP_ANGLES = np.linspace(-45.0, 45.0, 361)
_LOOKUP_DISTS = np.array([_ray_cast(a) for a in _LOOKUP_ANGLES])
if not np.isfinite(_LOOKUP_DISTS).all():
    good = np.isfinite(_LOOKUP_DISTS)
    _LOOKUP_DISTS = np.interp(_LOOKUP_ANGLES,
                              _LOOKUP_ANGLES[good], _LOOKUP_DISTS[good])


def wall_distance(angle_deg):
    """wall depth at a given spray angle. scalars or arrays."""
    angle_deg = np.asarray(angle_deg, dtype=float)
    return np.interp(angle_deg, _LOOKUP_ANGLES, _LOOKUP_DISTS)


# ---------------------------------------------------------
# FIELD GEOMETRY / LEGAL POSITIONS
# ---------------------------------------------------------

def build_grid(spacing=GRID_SPACING):
    """every legal outfield position on a grid."""
    max_r = _LOOKUP_DISTS.max()
    xs = np.arange(-max_r, max_r + spacing, spacing)
    ys = np.arange(0.0, max_r + spacing, spacing)
    X, Y = np.meshgrid(xs, ys)
    x, y = X.ravel(), Y.ravel()

    r = np.hypot(x, y)
    angle = np.degrees(np.arctan2(x, y))

    legal = (
        (r >= OUTFIELD_START)
        & (np.abs(angle) <= 45)
        & (r <= wall_distance(angle))
    )
    return np.column_stack([x[legal], y[legal]])


# ---------------------------------------------------------
# BALL RACE CONSTANTS (per ball, independent of fielders)
# ---------------------------------------------------------

# base locations, feet (home at origin, y toward CF)
_B = 90 / np.sqrt(2)
_SECOND_BASE = np.array([0.0, 2 * _B])
_THIRD_BASE = np.array([-_B, _B])


def ball_race_data(air):
    """per-ball quantities for the race model:
      stop_x, stop_y : where the ball ends up after rolling
      t_stop         : seconds from contact until the ball stops
      throw_2b/3b    : throw-flight seconds from the stop point"""
    lx = air['landing_x'].to_numpy()
    ly = air['landing_y'].to_numpy()
    hang = air['hang_time'].to_numpy()

    if 'landing_speed' in air.columns:
        v_land = air['landing_speed'].to_numpy()
    else:
        v_land = np.full(len(lx), 40.0)   # fallback for old cached data

    v_ground = ROLL_RETENTION * np.maximum(v_land, 0.0)
    roll = v_ground**2 / (2.0 * ROLL_FRICTION)
    t_roll = v_ground / ROLL_FRICTION

    landing_r = np.hypot(lx, ly)
    spray_angle = np.degrees(np.arctan2(lx, ly))
    stop_r = np.minimum(landing_r + roll, wall_distance(spray_angle))
    safe_r = np.where(landing_r > 1e-9, landing_r, 1e-9)
    scale = stop_r / safe_r
    sx, sy = lx * scale, ly * scale

    t_stop = hang + t_roll

    throw_2b = np.hypot(sx - _SECOND_BASE[0],
                        sy - _SECOND_BASE[1]) / THROW_SPEED
    throw_3b = np.hypot(sx - _THIRD_BASE[0],
                        sy - _THIRD_BASE[1]) / THROW_SPEED

    return sx, sy, t_stop, throw_2b, throw_3b


def stopping_points(air):
    """(stop_x, stop_y) only - kept for any external callers."""
    sx, sy, _, _, _ = ball_race_data(air)
    return sx, sy


# ---------------------------------------------------------
# PRECOMPUTED MATRICES (grid points x balls)
# ---------------------------------------------------------

def dist_matrix(points, air):
    """grid-to-LANDING distances (catch probability side)."""
    bx = air['landing_x'].to_numpy()
    by = air['landing_y'].to_numpy()
    return np.hypot(points[:, 0:1] - bx[None, :],
                    points[:, 1:2] - by[None, :])


def prob_matrix_for(dist, air, ability):
    """catch probability (0-100) for every (point, ball) pair for one
    specific fielder."""
    hang = air['hang_time'].to_numpy()
    reach = reachable_distance(hang, ability['bootup_feet'],
                               ability['sprint_speed'])
    return catch_prob_from_reach(dist, reach[None, :])


def pickup_matrix_for(points, air, ability, sx, sy, t_stop):
    """seconds from contact until THIS fielder has the ball in hand,
    for every (point, ball) pair: he runs to the stop point (his own
    bootup/sprint), but can't pick up before the ball stops there."""
    run_dist = np.hypot(points[:, 0:1] - sx[None, :],
                        points[:, 1:2] - sy[None, :])
    t_run = time_to_cover(run_dist, ability['bootup_feet'],
                          ability['sprint_speed'])
    return np.maximum(t_run, t_stop[None, :])


def infield_catchable(air, infield, prob_threshold=INFIELD_OUT_THRESHOLD):
    """boolean per ball: can a FIXED, league-average infielder catch it?"""
    positions = np.array([list(p) for p in infield.values()])
    bx = air['landing_x'].to_numpy()
    by = air['landing_y'].to_numpy()

    dist = np.hypot(positions[:, 0:1] - bx[None, :],
                    positions[:, 1:2] - by[None, :])
    prob = prob_matrix_for(dist, air, LEAGUE_AVG_ABILITY)
    return prob.max(axis=0) >= prob_threshold


# ---------------------------------------------------------
# SCORING: THE RACE MODEL
# ---------------------------------------------------------

_TIER_RUNS = np.array([0.0, DAMAGE['single'], DAMAGE['double'],
                       DAMAGE['triple']])


def race_tier(pickup, throw_2b, throw_3b):
    """hit tier from the race: when does the ball reach each base
    vs a fast runner?
      ball at 2B before the runner -> single
      else ball at 3B before the runner -> double
      else -> triple"""
    t_at_2b = pickup + TRANSFER_TIME + throw_2b
    t_at_3b = pickup + TRANSFER_TIME + throw_3b

    tier = np.full(pickup.shape, 3, dtype=int)
    tier = np.where(t_at_3b <= RUNNER_3B_TIME, 2, tier)
    tier = np.where(t_at_2b <= RUNNER_2B_TIME, 1, tier)
    return tier


def expected_runs(prob_cov, pickup_cov, throw_2b, throw_3b,
                  mode=MODE_MIN_RUNS):
    """expected runs allowed, summed over balls.
    in MODE_PREVENT_HIT the race model is irrelevant - an uncaught ball
    counts as 1 regardless of what it would turn into."""
    if mode == MODE_PREVENT_HIT:
        return (1 - prob_cov / 100.0).sum(axis=-1)
    tier = race_tier(pickup_cov, throw_2b, throw_3b)
    damage = _TIER_RUNS[tier]
    return ((1 - prob_cov / 100.0) * damage).sum(axis=-1)


def per_ball_expected_runs(air, fielders, abilities=None,
                           mode=MODE_MIN_RUNS):
    """expected runs (or, in MODE_PREVENT_HIT, expected hits) allowed on
    EACH ball at a given alignment."""
    abilities = abilities or {}
    bx = air['landing_x'].to_numpy()
    by = air['landing_y'].to_numpy()
    hang = air['hang_time'].to_numpy()
    sx, sy, t_stop, throw_2b, throw_3b = ball_race_data(air)

    prob_rows, pickup_rows = [], []
    for name, pos in fielders.items():
        ability = abilities.get(name, LEAGUE_AVG_ABILITY)
        land_dist = np.hypot(pos[0] - bx, pos[1] - by)
        reach = reachable_distance(hang, ability['bootup_feet'],
                                   ability['sprint_speed'])
        prob_rows.append(catch_prob_from_reach(land_dist, reach))

        run_dist = np.hypot(pos[0] - sx, pos[1] - sy)
        t_run = time_to_cover(run_dist, ability['bootup_feet'],
                              ability['sprint_speed'])
        pickup_rows.append(np.maximum(t_run, t_stop))

    prob_cov = np.maximum.reduce(prob_rows)
    pickup_cov = np.minimum.reduce(pickup_rows)

    if mode == MODE_PREVENT_HIT:
        return 1 - prob_cov / 100.0

    tier = race_tier(pickup_cov, throw_2b, throw_3b)
    damage = _TIER_RUNS[tier]
    return (1 - prob_cov / 100.0) * damage


# ---------------------------------------------------------
# HILL CLIMBING (per-fielder prob AND pickup surfaces)
# ---------------------------------------------------------

def refine_positions(chosen_idx, prob_list, pickup_list,
                     throw_2b, throw_3b, mode=MODE_MIN_RUNS):
    """local search: move each fielder to the grid point that most
    reduces the score (expected runs, or expected hits under
    MODE_PREVENT_HIT), others fixed. each fielder has his own
    catch-prob matrix AND his own pickup-time matrix."""
    chosen = list(chosen_idx)
    n_balls = throw_2b.shape[0]
    improved = True
    while improved:
        improved = False
        for i in range(len(chosen)):
            others = [j for j in range(len(chosen)) if j != i]
            if others:
                base_prob = np.maximum.reduce(
                    [prob_list[j][chosen[j]] for j in others])
                base_pick = np.minimum.reduce(
                    [pickup_list[j][chosen[j]] for j in others])
            else:
                base_prob = np.zeros(n_balls)
                base_pick = np.full(n_balls, 1e9)

            cand_prob = np.maximum(prob_list[i], base_prob[None, :])
            cand_pick = np.minimum(pickup_list[i], base_pick[None, :])
            scores = expected_runs(cand_prob, cand_pick,
                                   throw_2b[None, :], throw_3b[None, :],
                                   mode)

            best = int(np.argmin(scores))
            current = expected_runs(
                np.maximum(prob_list[i][chosen[i]], base_prob),
                np.minimum(pickup_list[i][chosen[i]], base_pick),
                throw_2b, throw_3b, mode
            )
            if scores[best] < current - 1e-9:
                chosen[i] = best
                improved = True
    return chosen


def _optimize_one_assignment(air, player_order, abilities, points,
                             land_dists, race, n_restarts, rng,
                             mode=MODE_MIN_RUNS):
    """optimize positions for one player->slot assignment."""
    sx, sy, t_stop, throw_2b, throw_3b = race
    prob_list, pickup_list = [], []
    for p in player_order:
        ability = abilities.get(p, LEAGUE_AVG_ABILITY)
        prob_list.append(prob_matrix_for(land_dists, air, ability))
        pickup_list.append(pickup_matrix_for(points, air, ability,
                                             sx, sy, t_stop))
    n_points, n_balls = land_dists.shape
    n_f = len(player_order)

    # greedy seed
    chosen = []
    cov_prob = np.zeros(n_balls)
    cov_pick = np.full(n_balls, 1e9)
    for i in range(n_f):
        cand_prob = np.maximum(prob_list[i], cov_prob[None, :])
        cand_pick = np.minimum(pickup_list[i], cov_pick[None, :])
        scores = expected_runs(cand_prob, cand_pick,
                               throw_2b[None, :], throw_3b[None, :], mode)
        idx = int(np.argmin(scores))
        chosen.append(idx)
        cov_prob = np.maximum(cov_prob, prob_list[i][idx])
        cov_pick = np.minimum(cov_pick, pickup_list[i][idx])
    starts = [chosen]

    for _ in range(n_restarts):
        starts.append(list(rng.choice(n_points, size=n_f, replace=False)))

    best_chosen, best_score = None, np.inf
    pbar = tqdm(starts, desc=" / ".join(player_order), leave=False)
    for start in pbar:
        climbed = refine_positions(start, prob_list, pickup_list,
                                   throw_2b, throw_3b, mode)
        cp = np.maximum.reduce(
            [prob_list[i][climbed[i]] for i in range(n_f)])
        ck = np.minimum.reduce(
            [pickup_list[i][climbed[i]] for i in range(n_f)])
        score = expected_runs(cp, ck, throw_2b, throw_3b, mode)
        if score < best_score:
            best_score, best_chosen = score, climbed
        pbar.set_postfix(best=f"{best_score:.2f}")

    return best_chosen, float(best_score)


def optimize_positions(air, players, abilities, center_player=None,
                       spacing=GRID_SPACING, n_restarts=10, seed=0,
                       mode=MODE_MIN_RUNS):
    """find the best (assignment, positions) for the given players.
    center_player set -> 'corners' mode; None -> 'all' mode.
    mode -> MODE_MIN_RUNS (minimize expected runs allowed) or
    MODE_PREVENT_HIT (maximize catches, ignoring what a miss turns into).
    returns (fielders_dict, assignment_dict, score, coverage)"""
    rng = np.random.default_rng(seed)
    points = build_grid(spacing)
    land_dists = dist_matrix(points, air)
    race = ball_race_data(air)

    others = [p for p in players if p != center_player]
    if center_player is not None:
        orderings = [
            [others[0], center_player, others[1]],
            [others[1], center_player, others[0]],
        ]
    else:
        orderings = [list(p) for p in permutations(players)]

    best = None
    for order in orderings:
        chosen, score = _optimize_one_assignment(
            air, order, abilities, points, land_dists, race,
            n_restarts, rng, mode)
        if best is None or score < best[2]:
            best = (order, chosen, score)

    order, chosen, score = best
    positions = points[chosen]

    sort_idx = np.argsort(positions[:, 0])
    names3 = ['LF', 'CF', 'RF']
    fielders, assignment = {}, {}
    for slot, i in enumerate(sort_idx):
        fielders[names3[slot]] = tuple(positions[i])
        assignment[names3[slot]] = order[i]

    prob_list = [prob_matrix_for(land_dists, air,
                                 abilities.get(p, LEAGUE_AVG_ABILITY))
                 for p in order]
    coverage = np.maximum.reduce(
        [prob_list[i][chosen[i]] for i in range(len(order))])

    return fielders, assignment, score, coverage