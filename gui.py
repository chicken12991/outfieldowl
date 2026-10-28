import io
import os
import re
import sys
import queue
import shutil
import threading
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog, messagebox

# force the non-window matplotlib backend BEFORE anything imports
# pyplot - figures are made in a background thread, which would
# crash the default interactive backend
import matplotlib
matplotlib.use('Agg')

from PIL import Image, ImageTk

import run_analysis as ra
from optimizer import (MODE_MIN_RUNS, MODE_PREVENT_HIT,
                       list_parks, set_park, current_park)
from fielder_data import load_fielder_ability
from field_plot import plot_park

PREVIEW_W, PREVIEW_H = 920, 620   # max plot preview size, pixels
POLL_MS = 100                     # how often the GUI reads the queue
WHEEL_STEP = 40                   # scroll delta per step (a mouse
                                  # notch is 120 = 3 steps)

# a printed line containing any of these goes to the Problems tab
PROBLEM_WORDS = ('league average', 'league-average', 'estimating',
                 'warning', 'error', 'failed', 'not on',
                 'no player found')

# name suffixes dropped from lineup lines ("Bobby Witt Jr." -> 2 words)
NAME_SUFFIXES = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv'}


def _to_terminal(text):
    """write straight to the real terminal (if there is one).
    never crashes - windows terminals choke on some accents."""
    stream = sys.__stderr__
    if stream is None:
        return
    try:
        stream.write(text)
        stream.flush()
    except Exception:
        pass


class QueueWriter:
    """stand-in for sys.stdout / sys.stderr. everything printed goes
    into the GUI's queue (for the Console tab) AND still goes to the
    real terminal. safe to call from the worker thread."""

    def __init__(self, q, real_stream):
        self.q = q
        self.real = real_stream
        self.encoding = 'utf-8'

    def write(self, text):
        if self.real is not None:
            try:
                self.real.write(text)
            except Exception:
                pass
        if text:
            self.q.put(('text', text))
        return len(text)

    def flush(self):
        if self.real is not None:
            try:
                self.real.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def _parse_lineup_line(line):
    """'Kyle Schwarber' -> ('Kyle', 'Schwarber').
    drops Jr./Sr./II/III. anything that isn't exactly two words
    after that raises a clear error (fails just that batter)."""
    parts = [p for p in line.split()
             if p.lower() not in NAME_SUFFIXES]
    if len(parts) != 2:
        raise ValueError(f"lineup line '{line}' must be exactly "
                         f"'First Last'")
    return parts[0], parts[1]


class BeisbolGUI:
    def __init__(self, root):
        self.root = root
        root.title("Beisbol - Outfield Positioning")

        self.q = queue.Queue()      # worker -> GUI messages
        self.plots = []             # (batter name, saved path)
        self.current = 0            # index into self.plots
        self.fails = []             # failure messages for this run
        self.auto_follow = True     # viewer tracks newest plot until
                                    # the user navigates manually
        self._img = None            # keep a reference or tk drops it
        self._park_cache = {}       # park key -> rendered preview image

        # log-tab state
        self.problem_count = 0
        self._line_buf = ''         # partial line waiting for '\n'
        self._cr_pending = False    # '\r' seen: next text overwrites
        self.who = 'setup'          # which batter problems belong to

        # scroll state: trackpads send many tiny deltas, we add them up
        self._wheel_accum = 0

        # ---------------- settings panel (left, scrollable) ----------------
        # the panel lives inside a canvas so it can scroll; the window's
        # height is set by the plot viewer, not by this panel
        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky='ns')
        left.rowconfigure(0, weight=1)

        self.left_canvas = tk.Canvas(left, highlightthickness=0,
                                     borderwidth=0, yscrollincrement=20)
        left_scroll = ttk.Scrollbar(left, orient='vertical',
                                    command=self.left_canvas.yview)
        self.left_canvas.configure(yscrollcommand=left_scroll.set)
        self.left_canvas.grid(row=0, column=0, sticky='ns')
        left_scroll.grid(row=0, column=1, sticky='ns')

        panel = ttk.Frame(self.left_canvas, padding=10)
        self.left_canvas.create_window((0, 0), window=panel, anchor='nw')
        panel.bind('<Configure>', lambda e: self.left_canvas.configure(
            scrollregion=self.left_canvas.bbox('all')))

        # lineup file
        ttk.Label(panel, text="Lineup file").grid(sticky='w')
        self.lineup_var = tk.StringVar(value=ra.LINEUP_FILE)
        row = ttk.Frame(panel)
        row.grid(sticky='ew', pady=(0, 8))
        ttk.Entry(row, textvariable=self.lineup_var, width=34)\
            .pack(side='left')
        ttk.Button(row, text="Browse", command=self.browse_lineup)\
            .pack(side='left', padx=4)

        # fielder checkboxes (pick exactly 3)
        ttk.Label(panel, text="Outfielders (pick 3)").grid(sticky='w')
        self.fielder_vars = {}
        for name in ra.PLAYER_IMAGES_BY_NAME:
            var = tk.BooleanVar(value=(name in ra.PLAYERS))
            ttk.Checkbutton(panel, text=name, variable=var)\
                .grid(sticky='w')
            self.fielder_vars[name] = var

        # ballpark - every park in mlbstadiums.csv
        ttk.Label(panel, text="Ballpark").grid(sticky='w', pady=(8, 0))
        self.parks = list_parks()          # display name -> team key
        default_label = next(
            (label for label, key in self.parks.items()
             if key == ra.PARK),
            next(iter(self.parks)))
        self.park_var = tk.StringVar(value=default_label)
        self.park_box = ttk.Combobox(panel, textvariable=self.park_var,
                                     state='readonly', height=16,
                                     values=list(self.parks), width=30)
        self.park_box.grid(sticky='w')
        # wheel over the dropdown does nothing (no accidental switches)
        self.park_box.bind('<MouseWheel>', lambda e: 'break')
        self.park_box.bind('<<ComboboxSelected>>', self.on_park_change)
        ttk.Label(panel, text="(wall outlines traced ~2023 - newer "
                              "wall changes aren't included)",
                  foreground='gray', wraplength=260).grid(sticky='w')

        # scoring mode
        ttk.Label(panel, text="Scoring mode").grid(sticky='w',
                                                   pady=(8, 0))
        self.mode_var = tk.StringVar(value=MODE_MIN_RUNS)
        ttk.Radiobutton(panel, text="Minimize expected runs",
                        variable=self.mode_var,
                        value=MODE_MIN_RUNS).grid(sticky='w')
        ttk.Radiobutton(panel, text="Prevent hits at all costs",
                        variable=self.mode_var,
                        value=MODE_PREVENT_HIT).grid(sticky='w')

        # run controls
        self.start_btn = ttk.Button(panel, text="Run lineup",
                                    command=self.start)
        self.start_btn.grid(sticky='ew', pady=(12, 4))

        self.progress = ttk.Progressbar(panel, mode='determinate')
        self.progress.grid(sticky='ew')
        self.status = ttk.Label(panel, text="ready", wraplength=260)
        self.status.grid(sticky='w', pady=(2, 0))

        # ---------------- log tabs (bottom of left panel) ----------------
        self.tabs = ttk.Notebook(panel)
        self.tabs.grid(sticky='nsew', pady=(10, 0))

        self.problems_text = self._make_log_tab('Problems (0)',
                                                ('Segoe UI', 9))
        self.console_text = self._make_log_tab('Console',
                                               ('Consolas', 8))
        self.problems_text.tag_configure(
            'who', foreground='gray30', font=('Segoe UI', 9, 'bold'))
        self.problems_text.tag_configure(
            'warn', foreground='#9a5b00')
        self.problems_text.tag_configure(
            'fail', foreground='#b00000', font=('Segoe UI', 9, 'bold'))

        # canvas is exactly as wide as the panel; its height is small on
        # purpose so it never makes the window taller - it just stretches
        panel.update_idletasks()
        self.left_canvas.configure(width=panel.winfo_reqwidth(),
                                   height=300)

        # mouse wheel / trackpad: handled for the whole window, then
        # routed by where the pointer actually is
        self.root.bind_all('<MouseWheel>', self._on_wheel, add='+')

        # ---------------- plot viewer (right) ----------------
        viewer = ttk.Frame(root, padding=10)
        viewer.grid(row=0, column=1, sticky='nsew')
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        self.plot_label = ttk.Label(
            viewer, text="plots appear here as they finish",
            anchor='center')
        self.plot_label.grid(row=0, column=0, columnspan=4,
                             sticky='nsew')
        viewer.rowconfigure(0, weight=1)
        viewer.columnconfigure(0, weight=1)

        nav = ttk.Frame(viewer)
        nav.grid(row=1, column=0, pady=6)
        self.prev_btn = ttk.Button(nav, text="< Prev",
                                   command=lambda: self.show(-1),
                                   state='disabled')
        self.prev_btn.pack(side='left')
        self.counter = ttk.Label(nav, text="0 / 0", width=12,
                                 anchor='center')
        self.counter.pack(side='left')
        self.next_btn = ttk.Button(nav, text="Next >",
                                   command=lambda: self.show(1),
                                   state='disabled')
        self.next_btn.pack(side='left')
        self.save_btn = ttk.Button(nav, text="Save all plots...",
                                   command=self.save_all,
                                   state='disabled')
        self.save_btn.pack(side='left', padx=12)

        # everything printed from here on also lands in the Console tab
        sys.stdout = QueueWriter(self.q, sys.__stdout__)
        sys.stderr = QueueWriter(self.q, sys.__stderr__)

        # show the default park until there's a real plot
        self.on_park_change()

        # the message pump runs for the life of the window
        self.root.after(POLL_MS, self.poll)

    # ---------------- park preview ----------------

    def on_park_change(self, event=None):
        """dropdown changed (or startup): make it the active park and,
        if no plots are showing yet, preview it."""
        key = self.parks.get(self.park_var.get(), ra.PARK)
        set_park(key)
        if not self.plots:
            self.show_park_preview()

    def show_park_preview(self):
        """draw the empty active park in the plot area. each park is
        drawn once, then reused from the cache."""
        key, name = current_park()
        img = self._park_cache.get(key)
        if img is None:
            try:
                buf = io.BytesIO()
                plot_park(name, buf)
                buf.seek(0)
                img = Image.open(buf)
                img.load()
                self._park_cache[key] = img
            except Exception as e:
                traceback.print_exc()
                self.plot_label.configure(
                    image='', text=f"couldn't draw park preview: {e}")
                return
        shown = img.copy()
        shown.thumbnail((PREVIEW_W, PREVIEW_H))
        self._img = ImageTk.PhotoImage(shown)
        self.plot_label.configure(image=self._img, text='')
        self.counter['text'] = "0 / 0"

    # ---------------- left panel scrolling ----------------

    def _on_wheel(self, event):
        """mouse wheel + trackpad scrolling for the left panel.
        windows doesn't always send the scroll to the widget under the
        pointer (sometimes it goes to whatever was clicked last), so we
        look up what's under the pointer ourselves.
          - pointer over a log box -> scroll that box
          - pointer anywhere else on the left panel -> scroll the panel
          - pointer on the plot side -> do nothing"""
        try:
            x, y = self.root.winfo_pointerxy()
            under = self.root.winfo_containing(x, y)
        except (KeyError, tk.TclError):
            return
        if under is None or \
                not str(under).startswith(str(self.left_canvas)):
            return

        # add up tiny trackpad deltas until they make a whole step
        self._wheel_accum += event.delta
        steps = int(self._wheel_accum / WHEEL_STEP)
        if steps == 0:
            return
        self._wheel_accum -= steps * WHEEL_STEP

        if isinstance(under, tk.Text):
            # if windows sent the event to this box, it already scrolled
            # itself; otherwise scroll it for it
            if under is not event.widget:
                under.yview_scroll(-steps, 'units')
            return

        if self.left_canvas.yview() == (0.0, 1.0):
            return   # everything fits, nothing to scroll
        self.left_canvas.yview_scroll(-steps, 'units')

    # ---------------- log tabs ----------------

    def _make_log_tab(self, title, font):
        """one notebook tab = a read-only text box + scrollbar."""
        frame = ttk.Frame(self.tabs)
        text = tk.Text(frame, wrap='word', width=1, height=14,
                       font=font, state='disabled', relief='flat',
                       padx=4, pady=4)
        scroll = ttk.Scrollbar(frame, orient='vertical',
                               command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tabs.add(frame, text=title)
        return text

    def _clear_logs(self):
        for w in (self.problems_text, self.console_text):
            w.configure(state='normal')
            w.delete('1.0', 'end')
            w.configure(state='disabled')
        self.problem_count = 0
        self._line_buf = ''
        self._cr_pending = False
        self.who = 'setup'
        self.tabs.tab(0, text='Problems (0)')

    def _console_write(self, text):
        """append to the Console tab like a real terminal: '\\r'
        means the next text overwrites the current line (that's how
        the tqdm progress bars update in place)."""
        w = self.console_text
        w.configure(state='normal')
        for piece in re.split(r'(\r|\n)', text):
            if piece == '\r':
                self._cr_pending = True
            elif piece == '\n':
                w.insert('end', '\n')
                self._cr_pending = False
            elif piece:
                if self._cr_pending:
                    w.delete('end-1c linestart', 'end-1c')
                    self._cr_pending = False
                w.insert('end', piece)
        w.see('end')
        w.configure(state='disabled')

    def _scan_for_problems(self, chunk):
        """collect printed text into full lines; any line with a
        problem word goes to the Problems tab."""
        self._line_buf += chunk
        while '\n' in self._line_buf:
            line, self._line_buf = self._line_buf.split('\n', 1)
            line = line.split('\r')[-1].strip()   # last redraw only
            low = line.lower()
            if line and any(word in low for word in PROBLEM_WORDS):
                self._add_problem(self.who, line, 'warn')

    def _add_problem(self, who, line, tag):
        w = self.problems_text
        w.configure(state='normal')
        w.insert('end', f"[{who}] ", 'who')
        w.insert('end', line + '\n', tag)
        w.see('end')
        w.configure(state='disabled')
        self.problem_count += 1
        self.tabs.tab(0, text=f'Problems ({self.problem_count})')

    # ---------------- settings actions ----------------

    def browse_lineup(self):
        path = filedialog.askopenfilename(
            title="Choose lineup file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.lineup_var.set(path)

    def start(self):
        players = [n for n, v in self.fielder_vars.items() if v.get()]
        if len(players) != 3:
            messagebox.showerror(
                "Fielders", f"Pick exactly 3 outfielders "
                            f"(you picked {len(players)}).")
            return
        lineup_path = self.lineup_var.get()
        if not os.path.exists(lineup_path):
            messagebox.showerror("Lineup",
                                 f"File not found:\n{lineup_path}")
            return

        center = 'Jacob Young' if 'Jacob Young' in players else None
        mode = self.mode_var.get()
        park_key = self.parks.get(self.park_var.get(), ra.PARK)

        self.plots.clear()
        self.fails = []
        self.current = 0
        self.auto_follow = True
        self.progress['value'] = 0
        self.start_btn['state'] = 'disabled'
        self.save_btn['state'] = 'disabled'
        self.park_box['state'] = 'disabled'   # no park swaps mid-run
        self.status['text'] = "loading fielder data..."
        self._clear_logs()

        # park preview stays up until the first real plot lands
        set_park(park_key)
        self.show_park_preview()

        t = threading.Thread(target=self.worker,
                             args=(lineup_path, players, center, mode,
                                   park_key),
                             daemon=True)
        t.start()

    # ---------------- background worker ----------------

    def worker(self, lineup_path, players, center, mode, park_key):
        """runs the whole lineup off the main thread. talks to the GUI
        only through the queue - never touches widgets directly."""
        try:
            set_park(park_key)

            with open(lineup_path, encoding='utf-8-sig') as f:
                lineup = [line.strip() for line in f if line.strip()]
            self.q.put(('total', len(lineup)))

            abilities = load_fielder_ability(players, year=2026)

            for line in lineup:
                self.q.put(('working', line))
                try:
                    first, last = _parse_lineup_line(line)
                    path = ra.analyze_batter(last, first, mode,
                                             abilities,
                                             players=players,
                                             center_player=center)
                    if path:
                        self.q.put(('plot', f"{first} {last}", path))
                    else:
                        self.q.put(('fail', line,
                                    "skipped - no air balls found in "
                                    "the date range"))
                except Exception as e:
                    tb = traceback.format_exc()
                    _to_terminal(tb)
                    self.q.put(('console', tb))
                    self.q.put(('fail', line, str(e)))
            self.q.put(('done',))
        except Exception as e:
            tb = traceback.format_exc()
            _to_terminal(tb)
            self.q.put(('console', tb))
            self.q.put(('fatal', str(e)))

    # ---------------- GUI-side message pump ----------------

    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == 'text':
                    self._console_write(msg[1])
                    self._scan_for_problems(msg[1])
                elif kind == 'console':
                    self._console_write(msg[1])
                elif kind == 'total':
                    self.progress['maximum'] = msg[1]
                elif kind == 'working':
                    self.who = msg[1]
                    self.status['text'] = f"analyzing {msg[1]}..."
                elif kind == 'plot':
                    self.plots.append((msg[1], msg[2]))
                    self.progress['value'] += 1
                    for b in (self.prev_btn, self.next_btn,
                              self.save_btn):
                        b['state'] = 'normal'
                    if self.auto_follow:
                        # viewer tracks the newest plot
                        self.current = len(self.plots) - 1
                        self.render()
                    else:
                        # user is browsing - just grow the counter
                        self.counter['text'] = \
                            f"{self.current + 1} / {len(self.plots)}"
                elif kind == 'fail':
                    self.progress['value'] += 1
                    self.fails.append(f"{msg[1]}: {msg[2]}")
                    self.status['text'] = f"failed: {msg[1]} - {msg[2]}"
                    self._add_problem(msg[1], f"FAILED - {msg[2]}",
                                      'fail')
                elif kind == 'done':
                    text = f"done - {len(self.plots)} plots in " \
                           f"{ra.PLOTS_DIR}"
                    if self.problem_count:
                        text += f"\n{self.problem_count} problem(s) - " \
                                f"see the Problems tab"
                        self.tabs.select(0)
                    self.status['text'] = text
                    self.start_btn['state'] = 'normal'
                    self.park_box['state'] = 'readonly'
                    if self.fails:
                        messagebox.showwarning(
                            "Some batters failed",
                            "\n".join(self.fails[:10]))
                elif kind == 'fatal':
                    self._add_problem('run', f"FAILED - {msg[1]}",
                                      'fail')
                    self.tabs.select(0)
                    self.start_btn['state'] = 'normal'
                    self.park_box['state'] = 'readonly'
                    messagebox.showerror("Run failed", msg[1])
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self.poll)

    # ---------------- viewer ----------------

    def show(self, step):
        """manual navigation - takes over from auto-follow for the
        rest of this run."""
        self.auto_follow = False
        if not self.plots:
            return
        self.current = (self.current + step) % len(self.plots)
        self.render()

    def render(self):
        try:
            name, path = self.plots[self.current]
            img = Image.open(path)
            img.thumbnail((PREVIEW_W, PREVIEW_H))
            self._img = ImageTk.PhotoImage(img)
            self.plot_label.configure(image=self._img, text='')
            self.counter['text'] = \
                f"{self.current + 1} / {len(self.plots)}"
        except Exception as e:
            self.status['text'] = f"couldn't display plot: {e}"

    def save_all(self):
        """copy ALL generated plots into a new timestamped folder
        inside a directory the user chooses. plots/ keeps its copies
        regardless - this is an extra export."""
        if not self.plots:
            return
        parent = filedialog.askdirectory(
            title="Choose where to put the export folder")
        if not parent:
            return
        stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        dest_dir = os.path.join(parent, f"outfieldowl_{stamp}")
        try:
            os.makedirs(dest_dir, exist_ok=True)
            for _, path in self.plots:
                shutil.copy(path, dest_dir)
            self.status['text'] = \
                f"saved {len(self.plots)} plots to {dest_dir}"
        except Exception as e:
            messagebox.showerror("Save failed", str(e))


if __name__ == '__main__':
    root = tk.Tk()
    BeisbolGUI(root)
    root.mainloop()