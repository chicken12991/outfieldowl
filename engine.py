"""
the analysis, split into two halves:

  prepare_batter   - SLOW, doesn't depend on any choices: savant pull +
                     hang-time physics. the weekly cloud job will run
                     this ahead of time and save the result.
  analyze_prepared - depends on the visitor's choices: park, fielders,
                     scoring mode. flags homers + infield outs for THAT
                     park, runs the optimizer, scores every ball.

the desktop app (run_analysis.py) just calls one after the other.
"""
import numpy as np

from batted_balls import get_air_balls
from fielders import add_catch_columns, DEFAULT_INFIELD
from optimizer import (optimize_positions, wall_distance,
                       per_ball_expected_runs, infield_catchable,
                       set_park, current_park)

# the only columns the analysis + plot use; saved data keeps just these
PREPARED_COLUMNS = [
    'game_date', 'events', 'bb_type',
    'launch_speed', 'launch_angle', 'hit_distance_sc',
    'hang_time', 'landing_speed',
    'landing_x', 'landing_y',
]


# ---------------------------------------------------------
# HALF 1: PREPARE (no choices involved)
# ---------------------------------------------------------

def prepare_batter(batter_id, start_dt, end_dt):
    """savant pull + per-ball drag/hang-time physics for one batter.
    returns a slim dataframe (PREPARED_COLUMNS only). can be empty.
    nothing in here depends on park, fielders or mode."""
    air = get_air_balls(batter_id, start_dt=start_dt, end_dt=end_dt)
    keep = [c for c in PREPARED_COLUMNS if c in air.columns]
    return air[keep].reset_index(drop=True)


# ---------------------------------------------------------
# HALF 2: ANALYZE (park, fielders, mode)
# ---------------------------------------------------------

def analyze_prepared(air, players, abilities, park, mode,
                     runner_times=None, center_player=None):
    """run the optimizer on prepared balls for the given choices.
      air           : dataframe from prepare_batter
      players       : the 3 outfielder names
      abilities     : {name: {'bootup_feet', 'sprint_speed'}}
      park          : park key, e.g. 'nationals'
      mode          : MODE_MIN_RUNS or MODE_PREVENT_HIT
      runner_times  : (t_2b, t_3b) for this batter, or None
      center_player : fixed CF name ('corners' mode), or None
    returns a dict:
      balls       : every ball + over_fence / infield_out / catch_prob /
                    nearest_fielder / exp_runs columns
      fielders    : {'LF': (x, y), 'CF': (x, y), 'RF': (x, y)}
      assignment  : {'LF': name, 'CF': name, 'RF': name}
      score       : total expected runs (or hits) allowed
      n_of_balls  : balls the outfield had to handle
      park_key, park_name"""
    if park != current_park()[0]:
        set_park(park)
    park_key, park_name = current_park()

    balls = air.copy()

    # flag balls the outfield doesn't have to worry about, for THIS park
    spray_angle = np.degrees(np.arctan2(balls['landing_x'],
                                        balls['landing_y']))
    landing_dist = np.hypot(balls['landing_x'], balls['landing_y'])
    balls['over_fence'] = landing_dist > wall_distance(spray_angle)
    balls['infield_out'] = infield_catchable(balls, DEFAULT_INFIELD)

    of_air = balls[~balls['over_fence'] & ~balls['infield_out']].copy()
    if len(of_air) == 0:
        raise ValueError("no balls left for the outfield after removing "
                         "homers and infield outs")

    best_fielders, assignment, score, _ = optimize_positions(
        of_air, players, abilities, center_player=center_player,
        mode=mode, runner_times=runner_times)

    # catch prob + per-ball score at the optimized alignment
    abilities_by_pos = {pos: abilities[assignment[pos]]
                        for pos in best_fielders}
    balls = add_catch_columns(balls, fielders=best_fielders,
                              abilities=abilities_by_pos)
    balls['exp_runs'] = per_ball_expected_runs(
        balls, best_fielders, abilities_by_pos, mode=mode,
        runner_times=runner_times)
    balls.loc[balls['over_fence'], 'catch_prob'] = 0
    balls.loc[balls['over_fence'], 'exp_runs'] = 0
    balls.loc[balls['infield_out'], 'exp_runs'] = 0

    return {
        'balls': balls,
        'fielders': best_fielders,
        'assignment': assignment,
        'score': score,
        'n_of_balls': len(of_air),
        'park_key': park_key,
        'park_name': park_name,
    }