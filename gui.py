import os
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
from optimizer import MODE_MIN_RUNS, MODE_PREVENT_HIT
from fielder_data import load_fielder_ability

PREVIEW_W, PREVIEW_H = 920, 620   # max plot preview size, pixels


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

        # ---------------- settings panel (left) ----------------
        panel = ttk.Frame(root, padding=10)
        panel.grid(row=0, column=0, sticky='ns')

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

        # ballpark (placeholder)
        ttk.Label(panel, text="Ballpark").grid(sticky='w', pady=(8, 0))
        self.park_var = tk.StringVar(value="Nationals Park")
        ttk.Combobox(panel, textvariable=self.park_var, state='readonly',
                     values=["Nationals Park"], width=30).grid(sticky='w')
        ttk.Label(panel, text="(park selection under development)",
                  foreground='gray').grid(sticky='w')

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

        self.plots.clear()
        self.fails = []
        self.current = 0
        self.auto_follow = True
        self.progress['value'] = 0
        self.start_btn['state'] = 'disabled'
        self.save_btn['state'] = 'disabled'
        self.status['text'] = "loading fielder data..."

        t = threading.Thread(target=self.worker,
                             args=(lineup_path, players, center, mode),
                             daemon=True)
        t.start()
        self.root.after(200, self.poll)

    # ---------------- background worker ----------------

    def worker(self, lineup_path, players, center, mode):
        """runs the whole lineup off the main thread. talks to the GUI
        only through the queue - never touches widgets directly."""
        try:
            with open(lineup_path) as f:
                lineup = [line.split() for line in f if line.strip()]
            self.q.put(('total', len(lineup)))

            abilities = load_fielder_ability(players, year=2026)

            for first, last in lineup:
                self.q.put(('working', f"{first} {last}"))
                try:
                    path = ra.analyze_batter(last, first, mode,
                                             abilities,
                                             players=players,
                                             center_player=center)
                    if path:
                        self.q.put(('plot', f"{first} {last}", path))
                    else:
                        self.q.put(('fail', f"{first} {last}",
                                    "no plot produced - is "
                                    "run_analysis.py the updated "
                                    "version that returns save_path?"))
                except Exception as e:
                    traceback.print_exc()
                    self.q.put(('fail', f"{first} {last}", str(e)))
            self.q.put(('done',))
        except Exception as e:
            traceback.print_exc()
            self.q.put(('fatal', str(e)))

    # ---------------- GUI-side message pump ----------------

    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == 'total':
                    self.progress['maximum'] = msg[1]
                elif kind == 'working':
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
                elif kind == 'done':
                    self.status['text'] = \
                        f"done - {len(self.plots)} plots in {ra.PLOTS_DIR}"
                    if self.fails:
                        messagebox.showwarning(
                            "Some batters failed",
                            "\n".join(self.fails[:10]))
                    self.start_btn['state'] = 'normal'
                    return
                elif kind == 'fatal':
                    messagebox.showerror("Run failed", msg[1])
                    self.start_btn['state'] = 'normal'
                    return
        except queue.Empty:
            pass
        self.root.after(200, self.poll)

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