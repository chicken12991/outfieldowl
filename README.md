# MLB Outfield Positioning Tool

A Python tool for modeling optimal outfield positioning for the Washington Nationals, built on real Statcast data.

## Overview

This project uses [pybaseball](https://github.com/jldbc/pybaseball) to pull Statcast data and models where outfielders should be positioned against a given batter. It compares the Nationals' outfield options — Jacob Young, James Wood, Dylan Crews, and Daylen Lile — using a physics-based catch model and a race-to-base scoring system.

## How It Works

- **Physics-based catch model**: estimates hang time and fielder retrieval time for batted balls based on launch angle, exit velocity, and trajectory.
- **Race-to-base scoring**: compares how long it takes a fielder to retrieve the ball against how long it takes the runner to reach the next base, to evaluate the risk/reward of different fielder positioning.
- **Hill-climbing optimizer**: searches for the outfield alignment that minimizes expected runs against a configurable batter.
- **Nationals Park wall geometry**: accounts for the actual dimensions and wall shape of the ballpark.

## Status

This is an active work-in-progress. Known issues and open tasks:

- **Bug**: left-field unguarded recommendation is currently inaccurate. Suspected cause is overly aggressive runner timing constants (`RUNNER_2B_TIME`, `RUNNER_3B_TIME`); a fix is planned but not yet implemented.
- **In progress**: hang-time-colored dot outlines in `field_plot.py` (visualization was cut off mid-rewrite).
- **Planned**: a `CHOOSE_BEST_TRIO` feature to recommend the best three-outfielder combination for a given matchup.

## Requirements

- Python 3.x
- [pybaseball](https://github.com/jldbc/pybaseball)
- (add any other dependencies your project uses, e.g. numpy, matplotlib, pandas)

## Setup

```bash
git clone <your-repo-url>
cd beisbol
pip install -r requirements.txt
```

*(If you don't have a `requirements.txt` yet, let me know and I can help generate one from your imports.)*

## Usage

*(Add instructions here for how to run the tool — e.g. what script to run and what arguments/config it expects.)*

## License

*(Add a license if you want this to be public, e.g. MIT.)*
