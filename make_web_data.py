"""
builds the static files the website needs. run from the beisbol folder:
    python make_web_data.py

writes:
  docs/parks.json        - park names + keys for the ballpark dropdown
  docs/parks/<key>.png   - empty-park preview image for every park

re-run it whenever the park data or the field drawing changes.
"""
import json
import os

import matplotlib
matplotlib.use('Agg')

from optimizer import list_parks, set_park, current_park
from field_plot import plot_park

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, 'docs')
PARK_IMG_DIR = os.path.join(DOCS, 'parks')


def main():
    os.makedirs(PARK_IMG_DIR, exist_ok=True)

    out = []
    for name, key in list_parks().items():
        set_park(key)
        _, shown_name = current_park()
        path = os.path.join(PARK_IMG_DIR, f"{key}.png")
        plot_park(shown_name, path)
        out.append({'name': name, 'key': key})
        print(f"drew {name} -> parks/{key}.png")

    with open(os.path.join(DOCS, 'parks.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f"\nwrote docs/parks.json ({len(out)} parks)")


if __name__ == '__main__':
    main()