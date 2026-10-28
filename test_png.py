"""step 3 test: run Schwarber through the engine and get the plot
back as png bytes in memory (the way the cloud version will),
then write those bytes to plots/test_output.png so we can look."""
import matplotlib
matplotlib.use('Agg')

import run_analysis as ra
from batted_balls import get_player_id
from engine import prepare_batter, analyze_prepared
from fielder_data import load_fielder_ability, load_runner_times
from fielders import DEFAULT_INFIELD
from field_plot import plot_alignment

players = ra.PLAYERS
batter_id = get_player_id('Schwarber', 'Kyle')

air = prepare_batter(batter_id, ra.START_DT, ra.END_DT)
abilities = load_fielder_ability(players)
runner_times = load_runner_times(batter_id, label='Kyle Schwarber')

result = analyze_prepared(air, players, abilities, park='nationals',
                          mode=ra.MODE_MIN_RUNS,
                          runner_times=runner_times,
                          center_player=ra.CENTER_PLAYER)

assignment = result['assignment']
png = plot_alignment(
    result['balls'], result['fielders'],
    title=f"TEST - expected runs allowed: {result['score']:.1f}",
    infield=DEFAULT_INFIELD,
    player_names={p: assignment[p] for p in result['fielders']},
    player_images={p: ra.PLAYER_IMAGES_BY_NAME.get(assignment[p])
                   for p in result['fielders']},
    show=False, return_png=True)

print(f"got back: {type(png).__name__}, {len(png):,} bytes")
with open('plots/test_output.png', 'wb') as f:
    f.write(png)
print("wrote plots/test_output.png")