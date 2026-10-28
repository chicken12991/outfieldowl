import numpy as np
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import matplotlib.patheffects as pe
from matplotlib.figure import Figure
from matplotlib.patches import Circle, Polygon
from matplotlib.lines import Line2D

import optimizer as _opt
from optimizer import wall_distance

GRASS_COLOR = '#4f9153'
DIRT_COLOR = '#c98a4b'
DIRT_EDGE = '#a06a35'


def _park_outline():
    """the active park's full traced outline (fair AND foul ground) in
    feet, or None if the park is the built-in 5-corner fallback, which
    has no foul-ground data. reads optimizer's active wall directly."""
    key, _ = _opt.current_park()
    if key == 'nationals_builtin' or _opt._WALL_XY is None:
        return None
    return _opt._WALL_XY


def _initials(name):
    """first initial + last initial: 'James Wood' -> 'JW'.
    single-word names fall back to their first two letters."""
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper()


def _draw_fielder(ax, x, y, initials):
    """fielder marker: black dot with the player's initials,
    centered exactly on (x, y)."""
    ax.scatter(x, y, s=350, color='black', zorder=4)
    ax.annotate(initials, (x, y), color='white', zorder=5,
                ha='center', va='center', fontsize=9, weight='bold')


def _draw_sidebar(fig, field_ax, sidebar_left, sidebar_right,
                  fielders, player_images, player_names,
                  exp_runs_by_pos=None):
    """stack of square photo cards on the right, exactly spanning the
    field's vertical extent. card size is derived from the field height
    so three always fit; sidebar width adjusts to keep cards square.
    each card: black border, white name band on top, photo below."""
    positions = list(fielders.keys())
    n = len(positions)

    # the field's box in figure coordinates
    fpos = field_ax.get_position()
    top, bottom = fpos.y1, fpos.y0
    total_h = top - bottom

    GAP = 0.025                              # gap between cards (fig coords)
    card_h = (total_h - GAP * (n - 1)) / n   # height that makes n cards fit

    # width that makes the card square in real inches
    fig_w, fig_h = fig.get_size_inches()
    card_w = card_h * (fig_h / fig_w)

    left = sidebar_left

    BAND = 0.15   # fraction of card height given to the name band

    for i, pos in enumerate(positions):
        y1 = top - i * (card_h + GAP)
        y0 = y1 - card_h
        ax = fig.add_axes([left, y0, card_w, card_h])

        # black border via the axis frame, drawn on top of the band
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_linewidth(1.8)
            spine.set_edgecolor('black')
            spine.set_zorder(10)

        name = player_names.get(pos, pos)
        img_path = player_images.get(pos)

        # ---- photo fills the card BELOW the name band ----
        if img_path is not None:
            try:
                img = mpimg.imread(img_path)
                ax.imshow(img, aspect='auto',
                          extent=[0, 1, 0, 1 - BAND])
            except (FileNotFoundError, OSError):
                pass

        # ---- name band across the top, inside the border ----
        ax.axhspan(1 - BAND, 1, facecolor='white', zorder=4)
        label = f"{pos} · {name}"
        if exp_runs_by_pos and pos in exp_runs_by_pos:
            label += f"  ({exp_runs_by_pos[pos]:.1f})"
        ax.text(0.5, 1 - BAND / 2, label, ha='center', va='center',
                fontsize=9, weight='bold', color='black', zorder=5,
                transform=ax.transAxes)

        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)


def _draw_field(ax):
    """the empty field for the ACTIVE park: grass, dirt, bases, mound,
    wall, foul lines, distance rings and spokes. shared by the real
    plots and the park preview so they always look identical."""
    angles = np.linspace(-45, 45, 721)
    rad = np.radians(angles)

    line_r_l, line_r_r = wall_distance(-45), wall_distance(45)
    u_l = np.array([np.sin(np.radians(-45)), np.cos(np.radians(-45))])
    u_r = np.array([np.sin(np.radians(45)),  np.cos(np.radians(45))])

    outline = _park_outline()
    if outline is not None:
        # ---- real park: grass fills the traced outline, which covers
        # fair AND foul ground; the outline itself is the wall ----
        ax.fill(outline[:, 0], outline[:, 1], color=GRASS_COLOR, zorder=0)
        loop = np.vstack([outline, outline[:1]])    # close the loop
        boundary_x, boundary_y = loop[:, 0], loop[:, 1]
    else:
        # ---- built-in fallback: fixed-width foul strip parallel to
        # each foul line (no real foul-ground data for this park) ----
        FOUL_WIDTH = 24   # ft, width of the foul-ground strip
        out_l = np.array([-1, -1]) / np.sqrt(2)   # outward normal, left
        out_r = np.array([ 1, -1]) / np.sqrt(2)   # outward normal, right

        left_home    = FOUL_WIDTH * out_l
        left_corner  = line_r_l * u_l + FOUL_WIDTH * out_l
        right_corner = line_r_r * u_r + FOUL_WIDTH * out_r
        right_home   = FOUL_WIDTH * out_r

        arc_x = wall_distance(angles) * np.sin(rad)
        arc_y = wall_distance(angles) * np.cos(rad)

        wedge_x = np.concatenate([[left_home[0], left_corner[0]], arc_x,
                                  [right_corner[0], right_home[0]]])
        wedge_y = np.concatenate([[left_home[1], left_corner[1]], arc_y,
                                  [right_corner[1], right_home[1]]])
        ax.fill(wedge_x, wedge_y, color=GRASS_COLOR, zorder=0)

        boundary_x = np.concatenate([wedge_x, [left_home[0]]])
        boundary_y = np.concatenate([wedge_y, [left_home[1]]])

    # ---- infield dirt skin: 95-ft circle on the mound, clipped so it
    # continues past each foul line in line with the basepath lanes,
    # ending where it meets the outfield grass ----
    b = 90 / np.sqrt(2)   # = 63.64 ft, x/y offset of the corner bases
    MOUND_DIST = 60.5
    DIRT_SKIN_RADIUS = 95
    LANE_W = 10   # ft, how far the dirt extends past each foul line
                  # (match this to the visual width of the basepath lane)

    dirt = Circle((0, MOUND_DIST), DIRT_SKIN_RADIUS,
                  facecolor=DIRT_COLOR, zorder=1)
    ax.add_patch(dirt)

    # clip region: everything between two lines parallel to the foul
    # lines, shifted LANE_W outward.
    corner = np.array([0, -LANE_W * np.sqrt(2)])  # offset lines meet here
    far = 600
    clip_poly = Polygon([corner,
                         corner + far * u_l,
                         [0, far],
                         corner + far * u_r],
                        closed=True, transform=ax.transData)
    dirt.set_clip_path(clip_poly)

    # infield grass, inside the base paths (the skin around it stays dirt)
    base_x = [0,  b,  0, -b, 0]
    base_y = [0,  b, 2*b, b, 0]
    ax.fill(base_x, base_y, color=GRASS_COLOR, zorder=2)

    # dirt basepath lanes, running back through the infield grass
    ax.plot(base_x, base_y, color=DIRT_COLOR, linewidth=13,
            solid_joinstyle='round', zorder=3)
    ax.plot(base_x, base_y, color='white', linewidth=1.5, zorder=4)

    # pitcher's mound + rubber
    mound = Circle((0, MOUND_DIST), radius=9, facecolor=DIRT_COLOR,
                   edgecolor=DIRT_EDGE, linewidth=1.2, zorder=3)
    ax.add_patch(mound)
    ax.plot([-1, 1], [MOUND_DIST, MOUND_DIST], color='white',
            linewidth=2, zorder=4)

    # bases
    ax.scatter([b, 0, -b], [b, 2*b, b], s=40, color='white',
               edgecolor='black', linewidth=0.8, marker='s', zorder=4)

    # outer boundary: the wall all the way around (real park) or the
    # wall + fixed foul strip (fallback)
    ax.plot(boundary_x, boundary_y, color='black', linewidth=2, zorder=2)

    # foul lines from home plate to the wall corners - outlined in black so
    # they read as chalk lines, distinct from the plain-white basepath lanes
    foul_line_effect = [pe.withStroke(linewidth=4, foreground='black')]
    ax.plot([0, line_r_l * u_l[0]], [0, line_r_l * u_l[1]],
            color='white', linewidth=2, zorder=2,
            path_effects=foul_line_effect)
    ax.plot([0, line_r_r * u_r[0]], [0, line_r_r * u_r[1]],
            color='white', linewidth=2, zorder=2,
            path_effects=foul_line_effect)

    # ---- radial distance grid, centered on home plate ----
    RING_STEP = 50
    max_r = wall_distance(0)
    rings = np.arange(RING_STEP, np.ceil(max_r / RING_STEP) * RING_STEP + 1, RING_STEP)
    LABEL_ANGLE = 48   # just outside the foul lines (45 deg)
    for r in rings:
        mask = r <= wall_distance(angles)
        if mask.any():
            ax.plot(r * np.sin(rad[mask]), r * np.cos(rad[mask]),
                    color='white', linewidth=0.6, linestyle=':',
                    alpha=0.6, zorder=1)
        for side in (-1, 1):
            label_rad = np.radians(side * LABEL_ANGLE)
            ax.annotate(f'{int(r)} ft',
                        (r * np.sin(label_rad), r * np.cos(label_rad)),
                        color='black', fontsize=7, weight='bold',
                        ha='center', va='center', zorder=6,
                        bbox=dict(facecolor='white', edgecolor='none',
                                  alpha=0.75, pad=1))

    # spokes from home plate out to the wall, every 15 degrees
    for spoke_angle in range(-30, 31, 15):
        spoke_rad = np.radians(spoke_angle)
        r = wall_distance(spoke_angle)
        ax.plot([0, r * np.sin(spoke_rad)], [0, r * np.cos(spoke_rad)],
                color='white', linewidth=0.6, linestyle=':',
                alpha=0.5, zorder=1)


def plot_park(park_name, save_path, figsize=(9, 8.5)):
    """the empty ACTIVE park - no balls, no fielders. used as the GUI
    preview before any plots exist. save_path can be a file path or a
    file-like object (io.BytesIO). uses a bare Figure (no pyplot) so
    it's safe to call from the GUI thread."""
    fig = Figure(figsize=figsize)
    ax = fig.add_axes([0.03, 0.03, 0.94, 0.86])
    _draw_field(ax)

    lf, cf, rf = (float(wall_distance(a)) for a in (-45, 0, 45))
    ax.set_title(f"{park_name}\n"
                 f"LF line {lf:.0f} ft  ·  CF {cf:.0f} ft  ·  "
                 f"RF line {rf:.0f} ft")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_aspect('equal')

    fig.savefig(save_path, format='png', dpi=100, bbox_inches='tight')


def plot_alignment(air, fielders, title="Optimal Outfield Alignment",
                   infield=None, player_images=None, player_names=None,
                   exp_runs_by_pos=None, save_path=None, show=True):
    """field on the left with initial-dot markers at each fielder's spot,
    square photo cards on the right identifying each player.
    - balls flagged 'over_fence' -> black x's (uncatchable)
    - balls flagged 'infield_out' -> gray dots (removed from OF problem)
    - if an 'exp_runs' column exists, in-play OF balls get labeled
    - 'infield' dict draws the fixed infielders as squares
    - 'player_images' maps position (LF/CF/RF) -> image path
    - 'player_names' maps position -> display name (full name)
    - 'exp_runs_by_pos' maps position -> expected-runs contribution"""

    player_images = player_images or {}
    player_names = player_names or {}

    fig = plt.figure(figsize=(13, 9))
    # field on the left; sidebar drawn manually in the right margin
    ax = fig.add_axes([0.06, 0.08, 0.62, 0.84])

    # match the field's frame to the sidebar borders
    for spine in ax.spines.values():
        spine.set_linewidth(1.8)

    # ---- the field itself (active park) ----
    _draw_field(ax)

    # ---- ball category masks ----
    over = air['over_fence'] if 'over_fence' in air.columns \
        else air['events'].eq('home_run')
    infield_out = air['infield_out'] if 'infield_out' in air.columns \
        else np.zeros(len(air), dtype=bool)

    # ---- infield-caught balls (gray) ----
    if infield_out.any():
        ax.scatter(air.loc[infield_out, 'landing_x'],
                   air.loc[infield_out, 'landing_y'],
                   s=18, color='lightgray', edgecolor='black',
                   linewidth=0.3, zorder=2)

    # ---- outfield balls: fill = catch probability,
    #      outline = hang time (red = line drive, green = pop-up) ----
    prob_colors = {
        100: '#1a9641',   # green - routine
        75:  '#a6d96a',   # light green
        50:  '#ffd92f',   # yellow - coin flip
        25:  '#fdae61',   # orange - diving territory
        0:   '#d7191c',   # red - uncatchable
    }
    HANG_LO, HANG_HI = 2.0, 6.0   # seconds mapped across the outline scale
    hang_cmap = plt.get_cmap('RdYlGn')
    hang_norm = plt.Normalize(vmin=HANG_LO, vmax=HANG_HI)

    of_ball = ~over & ~infield_out
    for prob, color in prob_colors.items():
        mask = of_ball & (air['catch_prob'] == prob)
        if mask.any():
            edge_colors = hang_cmap(hang_norm(
                air.loc[mask, 'hang_time'].to_numpy()))
            ax.scatter(air.loc[mask, 'landing_x'],
                       air.loc[mask, 'landing_y'],
                       s=26, color=color, edgecolor=edge_colors,
                       linewidth=1.4, zorder=3)

    # colorbar explaining the outline scale
    sm = plt.cm.ScalarMappable(cmap=hang_cmap, norm=hang_norm)
    cbar = fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.01, location='left')
    cbar.set_label('hang time (s) - outline color', fontsize=8)
    cbar.ax.tick_params(labelsize=7)

    # ---- over the fence (context only) ----
    ax.scatter(air.loc[over, 'landing_x'], air.loc[over, 'landing_y'],
               s=18, color='black', marker='x', linewidth=0.6, zorder=3)

    # ---- expected-runs labels on outfield balls ----
    if 'exp_runs' in air.columns:
        for _, row in air.loc[of_ball].iterrows():
            ax.annotate(f"{row['exp_runs']:.2f}",
                        (row['landing_x'], row['landing_y']),
                        fontsize=6, color='black', zorder=6,
                        xytext=(3, 3), textcoords='offset points')

    # ---- fixed infielders (squares, context) ----
    if infield is not None:
        for name, (x, y) in infield.items():
            ax.scatter(x, y, s=90, color='dimgray', marker='s', zorder=4)
            ax.annotate(name, (x, y), color='white', zorder=5,
                        ha='center', va='center', fontsize=6, weight='bold')

    # ---- outfielders as initial-dots ----
    for pos, (x, y) in fielders.items():
        name = player_names.get(pos, pos)
        _draw_fielder(ax, x, y, _initials(name) if name != pos else pos)

    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_aspect('equal')

    # legend built by hand: the ball markers' outline color encodes hang
    # time, not category, so an auto-generated legend would be misleading
    legend_handles = [Line2D([0], [0], marker='o', linestyle='None',
                             markersize=6, markerfacecolor='lightgray',
                             markeredgecolor='black', label='infield out')]
    for prob, color in prob_colors.items():
        legend_handles.append(Line2D([0], [0], marker='o', linestyle='None',
                                     markersize=6, markerfacecolor=color,
                                     markeredgecolor='black',
                                     label=f'{prob}%'))
    legend_handles.append(Line2D([0], [0], marker='x', linestyle='None',
                                 markersize=6, color='black',
                                 label='over fence'))
    ax.legend(handles=legend_handles, loc='lower right',
              title='catch prob', fontsize=8)

    # ---- photo sidebar (aligned to the field's height) ----
    _draw_sidebar(fig, ax, 0.72, 0.96,
                  fielders, player_images, player_names,
                  exp_runs_by_pos)

    if save_path is not None:
        fig.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"saved plot to {save_path}")
    if show:
        plt.show()
    plt.close(fig)