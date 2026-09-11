# Beisbol — MLB Outfield Positioning Optimizer

Recommends optimal outfield positioning for the Washington Nationals
against any MLB batter, using Statcast data and a physics-based model
of catching, rolling, and throwing. Produces an annotated field graphic
per batter showing recommended fielder positions and every air ball
colored by catch probability.

Built in Python with [pybaseball](https://github.com/jldbc/pybaseball)
(Statcast / Baseball Savant data).

## How it works (THIS IS NOT IN ITS FINAL STATE)

1. **Pull the batter's air balls** (fly balls + line drives) for a date
   range from Statcast.
2. **Reconstruct each ball's flight**: a 2D drag simulation is tuned
   per-ball until it matches the real hit distance, yielding hang time
   and landing speed (which drives roll distance after the bounce).
3. **Model each fielder's range** from their real Statcast metrics:
   jump (feet covered in the first 3 seconds) + sprint speed.
4. **Score any alignment in expected runs**: for each ball,
   `(1 - catch probability) x damage if it drops`. Damage comes from a
   race model — the retrieving fielder runs to the ball's stopping
   point, transfers, and throws; the hit becomes a single/double/triple
   depending on whether the throw beats a fast runner to second/third.
5. **Optimize**: hill climbing with random restarts over a 10-ft grid
   of legal outfield positions (real Nationals Park wall geometry),
   also choosing which player mans which spot.

## Setup

Requires Python 3.12+ and:

```
pip install pybaseball pandas numpy matplotlib tqdm
```

All `.py` files live in one folder, along with:

- **Player headshots** (optional): `jameswood.png`, `jacobyoung.png`,
  `dylancrews.png`, `daylenlile.png` — cropped square-ish headshots
  used in the graphic's sidebar. Missing images degrade gracefully.
- **`lineup.txt`** — the batters to analyze, one per line, in
  `last first` format:

```
turner trea
schwarber kyle
harper bryce
```

## Usage

Run everything from one file:

```
python run_analysis.py
```

This reads `lineup.txt`, analyzes each batter, prints the recommended
alignment + expected runs to the terminal, and saves a graphic per
batter (`<firstlast>_plot.png`) in the same folder, overwriting any
existing file. A progress bar shows optimization progress; the
Statcast downloads are the slow part (a few minutes for a full lineup).

### Configuration (top of `run_analysis.py`)

| Setting | What it does |
|---|---|
| `START_DT`, `END_DT` | Date range of batted-ball data to pull |
| `PLAYERS` | The three outfielders to position (full names) |
| `OPTIMIZE_MODE` | `'corners'` = fix the center fielder, try corner swaps. `'all'` = try every player-to-spot arrangement (3x slower) |
| `CENTER_PLAYER` | Who's fixed in the middle in corners mode |
| `PLAYER_IMAGES_BY_NAME` | Name -> headshot file mapping |

### Model tuning knobs (top of `optimizer.py`)

| Constant | Meaning |
|---|---|
| `GRID_SPACING` | Candidate-position grid resolution, feet |
| `DAMAGE` | Run values per hit type (linear weights) |
| `ROLL_RETENTION`, `ROLL_FRICTION` | Ground model: how far balls roll after landing |
| `THROW_SPEED`, `TRANSFER_TIME` | Outfielder throw model |
| `RUNNER_2B_TIME`, `RUNNER_3B_TIME` | The runner the defense races (see Known Issues) |
| `INFIELD_OUT_THRESHOLD` | Catch prob at which a fixed infielder claims a ball |
| `_WALL_CORNERS` | Park wall geometry — swap the table to model another park |

## Reading the graphic

- **Dot fill** = catch probability at the recommended alignment
  (green 100% -> red 0%)
- **Dot outline** = hang time (red = fast liner, green = high fly) —
  a red-filled, green-outlined dot is a catchable ball the alignment
  concedes; red-on-red was never catchable
- **Number label** = that ball's expected runs
- **Black X** = over the fence (excluded from optimization)
- **Gray dots** = balls the (fixed, league-average) infield handles
- **Black circles with initials** = recommended outfielder positions
- **Sidebar** = who's playing where; positions labeled LF/CF/RF by
  left-to-right order of the optimized spots

## File map

| File | Role |
|---|---|
| `run_analysis.py` | The only file you run — config + orchestration |
| `batted_balls.py` | Statcast pull, drag-sim physics, hang time, landing speed, coordinates |
| `fielder_data.py` | Pulls per-player jump + sprint speed from Savant leaderboards |
| `fielders.py` | Catch/reach model, fixed infield positions |
| `optimizer.py` | Park geometry, race scoring model, hill-climbing optimizer |
| `field_plot.py` | The output graphic |

## Known issues & limitations

- **Runner timing constants are too aggressive** (`RUNNER_2B_TIME = 7.9s`
  is elite, not 90th percentile). Symptom: deep balls get written off
  as "unavoidable doubles," which can make the optimizer comfortable
  leaving a field sector unguarded. Raising to ~8.4s / ~12.0s is the
  proposed fix, not yet applied.
- The single/double/triple race is a hard cutoff — a fielder who
  *almost* wins the race scores the same as one nowhere near the ball.
  A probabilistic boundary is the planned fix.
- Batter-only model: pitcher effects (handedness, pitch mix) are not
  yet considered.
- No throwing-arm differences between fielders (Statcast arm strength
  exists and could slot in), no wall caroms, no baserunner/out context,
  no foul-ball credit.
- Caught-ball circularity: for outs, Statcast's hit distance is where
  the catch happened, which partly reflects where real fielders stood.
- Roll model constants are hand-tuned, not fit to data.
