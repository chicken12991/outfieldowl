from pybaseball import statcast_outfielder_jump, statcast_sprint_speed

# league-average fallback for anyone missing from the leaderboards.
# ~32 ft bootup on 2-star plays, 27 ft/s sprint.
DEFAULT_ABILITY = {'bootup_feet': 32.0, 'sprint_speed': 27.0}


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