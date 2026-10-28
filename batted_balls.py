from pybaseball import statcast_batter, playerid_lookup, chadwick_register
import numpy as np
import pandas as pd

from fielder_data import plain_name


# ---------------------------------------------------------
# FLIGHT SIMULATION HELPERS
# ---------------------------------------------------------

def _simulate(v0_mph, angle_deg, cd):
    """simulate a 2D flight with drag.
    returns (distance_feet, time_seconds, landing_speed_horiz_fts)
    where landing_speed_horiz_fts is the HORIZONTAL speed at the
    moment the ball hits the ground, in ft/s (drives roll distance)."""
    m = 0.145      # baseball mass, kg
    rho = 1.225    # air density, kg/m^3
    A = 0.00426    # cross-section area, m^2
    g = 9.81       # gravity, m/s^2

    v0 = v0_mph * 0.447          # mph -> m/s
    theta = np.radians(angle_deg)
    vx = v0 * np.cos(theta)
    vy = v0 * np.sin(theta)

    x, y = 0.0, 1.0    # start ~1m off the ground
    dt = 0.01
    t = 0.0

    while y > 0:
        speed = np.hypot(vx, vy)
        drag = 0.5 * rho * cd * A * speed**2 / m
        vx -= drag * (vx / speed) * dt
        vy -= (drag * (vy / speed) + g) * dt
        x += vx * dt
        y += vy * dt
        t += dt

    return x * 3.281, t, vx * 3.281   # feet, seconds, ft/s horizontal


def _solve_cd(v0_mph, angle_deg, actual_dist):
    """find the drag coefficient that makes the sim match the real distance"""
    lo, hi = 0.0, 2.0
    for _ in range(40):
        mid = (lo + hi) / 2
        sim_dist, _, _ = _simulate(v0_mph, angle_deg, mid)
        if sim_dist > actual_dist:
            lo = mid    # sim went too far -> more drag
        else:
            hi = mid    # sim fell short -> less drag
    return (lo + hi) / 2


# ---------------------------------------------------------
# ID LOOKUP
# ---------------------------------------------------------

def _squash(s):
    """plain_name, then drop periods and spaces too.
    'J. T.' -> 'jt', 'Arráez' -> 'arraez'."""
    return plain_name(s).replace('.', '').replace(' ', '')


def get_player_id(last_name, first_name):
    """look up a player's mlbam id by name.
    1) normal pybaseball lookup with accents stripped
    2) if that finds nobody, search the whole chadwick register,
       comparing names with accents, periods and spaces removed
       (so 'Arraez' finds 'Arráez' and 'JT' finds 'J. T.')
    if there are multiple matches, uses the most recent player."""
    lookup = playerid_lookup(plain_name(last_name), plain_name(first_name))

    if lookup.empty:
        reg = chadwick_register()
        lasts = reg['name_last'].fillna('').map(_squash)
        firsts = reg['name_first'].fillna('').map(_squash)
        lookup = reg[(lasts == _squash(last_name))
                     & (firsts == _squash(first_name))]
        # keep only rows that actually have an mlbam id
        lookup = lookup[lookup['key_mlbam'].fillna(-1) > 0]
        if not lookup.empty:
            print(f"note: found {first_name} {last_name} with the "
                  f"backup name search")

    if lookup.empty:
        raise ValueError(f"no player found for {first_name} {last_name}")
    if len(lookup) > 1:
        lookup = lookup.sort_values('mlb_played_first', ascending=False,
                                    na_position='last')
        print(f"warning: multiple matches for {first_name} {last_name}, "
              f"using most recent (mlbam id {lookup.iloc[0]['key_mlbam']})")
    return int(lookup.iloc[0]['key_mlbam'])


# ---------------------------------------------------------
# MAIN FUNCTION
# ---------------------------------------------------------

def get_air_balls(player_id, start_dt, end_dt):
    """pull statcast data for one batter and return a dataframe of
    their air balls (fly balls + line drives) with:
      - flight_time   : vacuum time of flight (no drag)
      - vacuum_dist   : vacuum distance
      - dist_ratio    : actual / vacuum distance
      - cd_est        : estimated drag coefficient per ball
      - hang_time     : drag-corrected time of flight
      - landing_speed : horizontal speed at ground contact, ft/s
                        (drives how far the ball rolls after landing)
      - landing_x/y   : landing spot in feet (home plate at origin,
                        y pointing toward center field)
    """
    # pull just this batter (faster than pulling the whole league)
    data = statcast_batter(start_dt=start_dt, end_dt=end_dt,
                           player_id=player_id)

    # balls in play only
    batted = data[data['hc_x'].notna()].copy()

    # vacuum physics
    speed_fts = batted['launch_speed'] * 1.467
    angle_rad = np.radians(batted['launch_angle'])
    vy = speed_fts * np.sin(angle_rad)
    batted['flight_time'] = ((2 * vy) / 32.17).clip(lower=0)
    batted['vacuum_dist'] = (speed_fts**2) * np.sin(2 * angle_rad) / 32.17
    batted['dist_ratio'] = batted['hit_distance_sc'] / batted['vacuum_dist']

    # air balls only
    air = batted[
        (batted['bb_type'].isin(['fly_ball', 'line_drive']))
        & (batted['launch_angle'] > 10)
        & (batted['hit_distance_sc'].notna())
        & (batted['launch_speed'].notna())
    ].copy()

    # per-ball drag coefficient, then hang time + landing speed from
    # one final simulation at the solved cd
    air['cd_est'] = air.apply(
        lambda row: _solve_cd(row['launch_speed'], row['launch_angle'],
                              row['hit_distance_sc']),
        axis=1
    )
    sim_results = air.apply(
        lambda row: _simulate(row['launch_speed'], row['launch_angle'],
                              row['cd_est']),
        axis=1
    )
    air['hang_time'] = [r[1] for r in sim_results]
    air['landing_speed'] = [r[2] for r in sim_results]

    # landing coordinates in feet
    air['landing_x'] = 2.5 * (air['hc_x'] - 125.42)
    air['landing_y'] = 2.5 * (198.27 - air['hc_y'])

    return air