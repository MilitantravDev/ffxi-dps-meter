"""DPS Meter - external window fed by the DPSLink Windower addon.

Run:  python dpsmeter.py      (or double-click DPSMeter.bat / dpsmeter.pyw)
Only the Python standard library is used (tkinter).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import tkinter as tk
from tkinter import ttk

from engine import CAT_LABEL, CAT_ORDER, Engine, Filters, fmt_num, fmt_time

if getattr(sys, "frozen", False):
    # Packaged exe: keep settings next to DPSMeter.exe, not in the temp unpack folder.
    HERE = os.path.dirname(os.path.abspath(sys.executable))
    RESOURCES = getattr(sys, "_MEIPASS", HERE)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    RESOURCES = HERE
SETTINGS_FILE = os.path.join(HERE, "settings.json")
ICON_FILE = os.path.join(RESOURCES, "dpsmeter.ico")

DEFAULTS = {
    "bind": "127.0.0.1",   # use 0.0.0.0 to accept characters from other PCs
    "port": 24250,
    "timeout": 8,
    "geometry": "820x440+200+200",
    "alpha": 0.92,
    "topmost": True,
    "compact": False,
    "show_pets": True,
    "show_trusts": True,
    "show_alliance": True,
    "merge_pets": True,
    "rows": 12,
}

BG, PANEL, FG, DIM, ACCENT = "#15171c", "#1f232b", "#e6e6e6", "#8a8f99", "#ffc850"
KIND_COLOR = {
    "me": "#4caf50", "party": "#4f8fd9", "alliance": "#7b6fd6",
    "trust": "#d9a43c", "pet": "#c06fd6",
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            s.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=2)
    except OSError:
        pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.s = load_settings()
        self.engine = Engine(timeout=float(self.s["timeout"]))
        self.filters = Filters(show_pets=self.s["show_pets"], show_trusts=self.s["show_trusts"],
                               show_alliance=self.s["show_alliance"], merge_pets=self.s["merge_pets"])
        self.view = "current"          # 'current' | 'all' | combat id
        self.selected: str | None = None
        self.row_hit: list[tuple[int, int, str]] = []
        self._last_drawn = None
        self._view_ids: list = []
        self._panels_at = 0.0

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((self.s["bind"], int(self.s["port"])))
            self.listen_err = None
        except OSError as e:
            self.listen_err = str(e)
        self.sock.setblocking(False)

        self._build_ui()
        self.root.after(30, self._poll)
        self.root.after(250, self._refresh)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        r = self.root
        r.title("DPS Meter")
        if os.path.exists(ICON_FILE):
            try:
                r.iconbitmap(default=ICON_FILE)
            except tk.TclError:
                pass
        r.configure(bg=BG)
        r.geometry(self.s["geometry"])
        r.attributes("-topmost", bool(self.s["topmost"]))
        r.attributes("-alpha", float(self.s["alpha"]))
        r.protocol("WM_DELETE_WINDOW", self._on_close)

        st = ttk.Style(r)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL,
                     lightcolor=PANEL, darkcolor=PANEL, troughcolor=PANEL, font=("Segoe UI", 9))
        st.configure("TButton", background=PANEL, padding=(6, 2))
        st.map("TButton", background=[("active", "#2c313b")])
        st.configure("TCheckbutton", background=BG)
        st.map("TCheckbutton", background=[("active", BG)])
        st.configure("TCombobox", arrowcolor=FG, foreground=FG)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=20,
                     font=("Consolas", 9))
        st.configure("Treeview.Heading", background=BG, foreground=DIM, font=("Segoe UI", 8, "bold"))
        st.map("Treeview", background=[("selected", "#34495e")])
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=PANEL, foreground=DIM, padding=(10, 3))
        st.map("TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", FG)])
        r.option_add("*TCombobox*Listbox.background", PANEL)
        r.option_add("*TCombobox*Listbox.foreground", FG)

        # --- toolbar
        self.toolbar = tk.Frame(r, bg=BG)
        self.toolbar.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Button(self.toolbar, text="◀", width=3, command=lambda: self._step(-1)).pack(side="left")
        self.view_var = tk.StringVar()
        self.view_box = ttk.Combobox(self.toolbar, textvariable=self.view_var, state="readonly", width=30)
        self.view_box.pack(side="left", padx=3)
        self.view_box.bind("<<ComboboxSelected>>", self._on_view_pick)
        ttk.Button(self.toolbar, text="▶", width=3, command=lambda: self._step(1)).pack(side="left")
        ttk.Button(self.toolbar, text="Live", width=5, command=lambda: self._set_view("current")).pack(side="left", padx=(6, 0))
        ttk.Button(self.toolbar, text="All", width=4, command=lambda: self._set_view("all")).pack(side="left", padx=2)

        for txt, cmd in (("Compact", self._toggle_compact), ("Pin", self._toggle_topmost),
                         ("Copy", self._copy_report), ("Reset", self._reset), ("End", self.engine.end_combat)):
            ttk.Button(self.toolbar, text=txt, width=len(txt) + 1, command=cmd).pack(side="right", padx=1)

        # --- filter bar
        self.filterbar = tk.Frame(r, bg=BG)
        self.filterbar.pack(fill="x", padx=6, pady=2)
        tk.Label(self.filterbar, text="Mob", bg=BG, fg=DIM).pack(side="left")
        self.mob_var = tk.StringVar()
        self.mob_var.trace_add("write", lambda *_: self._apply_filters())
        e = tk.Entry(self.filterbar, textvariable=self.mob_var, width=14, bg=PANEL, fg=FG,
                     insertbackground=FG, relief="flat")
        e.pack(side="left", padx=(3, 8))
        tk.Label(self.filterbar, text="Seen by", bg=BG, fg=DIM).pack(side="left")
        self.src_var = tk.StringVar(value="All characters")
        self.src_box = ttk.Combobox(self.filterbar, textvariable=self.src_var, state="readonly", width=14)
        self.src_box.pack(side="left", padx=(3, 8))
        self.src_box.bind("<<ComboboxSelected>>", lambda _: self._apply_filters())

        self.cat_vars = {}
        for cat in CAT_ORDER:
            v = tk.BooleanVar(value=True)
            ttk.Checkbutton(self.filterbar, text=cat, variable=v, command=self._apply_filters).pack(side="left")
            self.cat_vars[cat] = v

        self.filterbar2 = tk.Frame(r, bg=BG)
        self.filterbar2.pack(fill="x", padx=6, pady=(0, 2))
        self.flag_vars = {}
        for key, label in (("show_pets", "Pets"), ("show_trusts", "Trusts"),
                           ("show_alliance", "Alliance"), ("merge_pets", "Merge pets into owner")):
            v = tk.BooleanVar(value=bool(self.s[key]))
            ttk.Checkbutton(self.filterbar2, text=label, variable=v, command=self._apply_filters).pack(side="left")
            self.flag_vars[key] = v
        tk.Label(self.filterbar2, text="Combat timeout (s)", bg=BG, fg=DIM).pack(side="left", padx=(12, 3))
        self.timeout_var = tk.StringVar(value=str(self.s["timeout"]))
        sp = tk.Spinbox(self.filterbar2, from_=2, to=120, width=4, textvariable=self.timeout_var,
                        command=self._apply_timeout, bg=PANEL, fg=FG, buttonbackground=PANEL, relief="flat")
        sp.pack(side="left")
        sp.bind("<FocusOut>", lambda _: self._apply_timeout())
        tk.Label(self.filterbar2, text="Opacity", bg=BG, fg=DIM).pack(side="left", padx=(12, 3))
        self.alpha_var = tk.DoubleVar(value=float(self.s["alpha"]))
        ttk.Scale(self.filterbar2, from_=0.3, to=1.0, variable=self.alpha_var, length=90,
                  command=lambda v: r.attributes("-alpha", float(v))).pack(side="left")

        # --- body
        self.body = tk.PanedWindow(r, orient="horizontal", bg=BG, sashwidth=4, bd=0)
        self.body.pack(fill="both", expand=True, padx=6, pady=2)

        left = tk.Frame(self.body, bg=BG)
        self.header = tk.Label(left, text="", bg=BG, fg=FG, anchor="w", font=("Segoe UI", 10, "bold"))
        self.header.pack(fill="x")
        self.subheader = tk.Label(left, text="", bg=BG, fg=DIM, anchor="w", font=("Segoe UI", 9))
        self.subheader.pack(fill="x")
        self.canvas = tk.Canvas(left, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=(2, 0))
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Configure>", lambda _: self._redraw(force=True))
        self.body.add(left, minsize=260, stretch="always")

        self.right = ttk.Notebook(self.body)
        self.bd_tree = self._make_tree(self.right, ("dmg", "pct", "hits", "avg", "max", "acc", "crit"),
                                       ("Damage", "%", "Hits", "Avg", "Max", "Acc", "Crit"), tree_label="Source")
        self.right.add(self.bd_tree.master, text="Breakdown")
        self.cb_tree = self._make_tree(self.right, ("mob", "time", "dmg", "dps", "top"),
                                       ("Main target", "Time", "Damage", "DPS", "Top"), tree_label="#")
        self.cb_tree.bind("<<TreeviewSelect>>", self._on_combat_pick)
        self.right.add(self.cb_tree.master, text="Combats")
        self.mob_tree = self._make_tree(self.right, ("dmg", "pct"), ("Damage", "%"), tree_label="Mob")
        self.mob_tree.bind("<Double-1>", self._on_mob_pick)
        self.right.add(self.mob_tree.master, text="Mobs")
        self.body.add(self.right, minsize=300, stretch="always")

        # --- status
        self.status = tk.Label(r, text="", bg=BG, fg=DIM, anchor="w", font=("Segoe UI", 8))
        self.status.pack(fill="x", padx=6, pady=(0, 4))

        if self.s["compact"]:
            self.s["compact"] = False
            self._toggle_compact()

    def _make_tree(self, parent, cols, heads, tree_label):
        frame = tk.Frame(parent, bg=BG)
        tree = ttk.Treeview(frame, columns=cols, show="tree headings")
        tree.heading("#0", text=tree_label, anchor="w")
        tree.column("#0", width=150, stretch=True)
        for c, h in zip(cols, heads):
            tree.heading(c, text=h, anchor="e")
            w = 140 if c in ("mob", "top") else 60
            tree.column(c, width=w, anchor="w" if c in ("mob", "top") else "e", stretch=c in ("mob", "top"))
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tree.tag_configure("cat", foreground=ACCENT)
        tree.tag_configure("active", foreground="#ff7070")
        return tree

    # ------------------------------------------------------------- actions
    def _set_view(self, v):
        self.view = v
        self._redraw(force=True)

    def _step(self, d):
        ids = [c.id for c in self.engine.combats]
        if not ids:
            return
        idx = ids.index(self.view) if self.view in ids else len(ids) - 1
        idx = max(0, min(len(ids) - 1, idx + d))
        self._set_view(ids[idx])

    def _on_view_pick(self, _):
        i = self.view_box.current()
        if 0 <= i < len(self._view_ids):
            self._set_view(self._view_ids[i])

    def _on_combat_pick(self, _):
        sel = self.cb_tree.selection()
        if sel:
            self.view = int(sel[0])
            self._redraw(force=True, keep_combat_tree=True)

    def _on_mob_pick(self, _):
        sel = self.mob_tree.selection()
        if sel:
            mob = self.mob_tree.item(sel[0], "text")
            self.mob_var.set("" if self.mob_var.get() == mob else mob)

    def _on_canvas_click(self, ev):
        for y0, y1, name in self.row_hit:
            if y0 <= ev.y <= y1:
                self.selected = None if self.selected == name else name
                self.right.select(0)
                self._redraw(force=True)
                return

    def _apply_filters(self):
        f = self.filters
        f.mob = self.mob_var.get().strip()
        sel = [c for c, v in self.cat_vars.items() if v.get()]
        f.cats = None if len(sel) == len(CAT_ORDER) else set(sel)
        src = self.src_var.get()
        f.source = None if src in ("", "All characters") else src
        for k, v in self.flag_vars.items():
            setattr(f, k, v.get())
            self.s[k] = v.get()
        self._redraw(force=True)

    def _apply_timeout(self):
        try:
            self.engine.timeout = max(1.0, float(self.timeout_var.get()))
            self.s["timeout"] = self.engine.timeout
        except ValueError:
            pass

    def _toggle_compact(self):
        self.s["compact"] = not self.s["compact"]
        if self.s["compact"]:
            self.filterbar.pack_forget()
            self.filterbar2.pack_forget()
            self.body.forget(self.right)
            self.status.pack_forget()
        else:
            self.filterbar.pack(fill="x", padx=6, pady=2, after=self.toolbar)
            self.filterbar2.pack(fill="x", padx=6, pady=(0, 2), after=self.filterbar)
            self.body.add(self.right, minsize=300, stretch="always")
            self.status.pack(fill="x", padx=6, pady=(0, 4))
        self._redraw(force=True)

    def _toggle_topmost(self):
        self.s["topmost"] = not self.s["topmost"]
        self.root.attributes("-topmost", self.s["topmost"])

    def _reset(self):
        self.engine.reset()
        self.view = "current"
        self.selected = None
        self._redraw(force=True)

    def _copy_report(self):
        combats = self.engine.select(self.view)
        rows, total, dur = self.engine.aggregate(combats, self.filters)
        lines = [f"DPS {self._title(combats)} ({fmt_time(dur)}) total {fmt_num(total)}"]
        for i, r in enumerate(rows[:8], 1):
            lines.append(f"{i}. {r.name} {fmt_num(r.damage)} ({100 * r.damage / total:.1f}%) {r.damage / dur:.1f} dps")
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self.status.config(text="Report copied to clipboard.")

    def _on_close(self):
        self.s["geometry"] = self.root.geometry()
        self.s["alpha"] = round(float(self.alpha_var.get()), 2)
        save_settings(self.s)
        self.root.destroy()

    # ---------------------------------------------------------------- loop
    def _poll(self):
        for _ in range(500):
            try:
                data, _addr = self.sock.recvfrom(65535)
            except (BlockingIOError, OSError):
                break
            self.engine.feed_raw(data)
        self.root.after(30, self._poll)

    def _refresh(self):
        self.engine.tick()
        self._redraw()
        self.root.after(250, self._refresh)

    # ----------------------------------------------------------- rendering
    def _title(self, combats):
        if self.view == "all":
            return f"All fights ({len(combats)})"
        if not combats:
            return "Waiting for combat…"
        c = combats[0]
        return f"#{c.id} {c.primary_mob(self.filters.mob_ok)}"

    def _redraw(self, force=False, keep_combat_tree=False):
        e = self.engine
        live = e.current is not None and (self.view in ("current", "all") or self.view == e.current.id)
        sig = (e.version, self.view, self.selected)
        if not force and not live and sig == self._last_drawn:
            self._draw_status()
            return
        self._last_drawn = sig
        combats = e.select(self.view)
        rows, total, dur = e.aggregate(combats, self.filters)

        state = "  ● live" if live and e.current in combats else ""
        self.header.config(text=self._title(combats) + state, fg="#ff7070" if state else FG)
        self.subheader.config(text=f"{fmt_time(dur)}   total {fmt_num(total)}   raid DPS {total / dur:,.1f}")
        self._draw_meter(rows, total, dur)
        if not self.s["compact"] and (force or time.time() - self._panels_at > 1.0):
            self._panels_at = time.time()
            self._draw_views()
            self._draw_breakdown(combats, total, dur)
            if not keep_combat_tree:
                self._draw_combats()
            self._draw_mobs(combats)
            self._draw_sources()
        self._draw_status()

    def _draw_meter(self, rows, total, dur):
        cv = self.canvas
        cv.delete("all")
        self.row_hit = []
        w = max(cv.winfo_width(), 200)
        h = 22
        top = rows[0].damage if rows else 1
        for i, r in enumerate(rows[: int(self.s["rows"])]):
            y = i * (h + 2)
            color = KIND_COLOR.get(r.kind, KIND_COLOR["party"])
            cv.create_rectangle(0, y, w, y + h, fill=PANEL, width=0)
            cv.create_rectangle(0, y, max(2, w * r.damage / top), y + h, fill=color, width=0)
            if r.name == self.selected:
                cv.create_rectangle(1, y + 1, w - 1, y + h - 1, outline=ACCENT, width=2)
            cv.create_text(6, y + h / 2, text=f"{i + 1}. {r.name}", anchor="w", fill="white",
                           font=("Segoe UI", 9, "bold"))
            cv.create_text(w - 6, y + h / 2, anchor="e", fill="white", font=("Consolas", 9),
                           text=f"{fmt_num(r.damage)}  {r.damage / dur:,.1f}  {100 * r.damage / total:4.1f}%")
            self.row_hit.append((y, y + h, r.name))
        if not rows:
            msg = "No data yet." if not self.listen_err else f"Cannot listen on port: {self.listen_err}"
            cv.create_text(w / 2, 30, text=msg, fill=DIM, font=("Segoe UI", 10))

    def _draw_views(self):
        labels, ids = ["Live (current / last)", "All fights"], ["current", "all"]
        for c in reversed(self.engine.combats):
            labels.append(f"#{c.id} {c.primary_mob()}  {fmt_time(c.duration())}{'  ● live' if c.active else ''}")
            ids.append(c.id)
        self._view_ids = ids
        if list(self.view_box["values"]) != labels:
            self.view_box["values"] = labels
        if self.view in ids:
            self.view_box.current(ids.index(self.view))

    def _draw_breakdown(self, combats, total_all, dur):
        t = self.bd_tree
        t.delete(*t.get_children())
        if not self.selected:
            t.insert("", "end", text="Click a player", values=())
            return
        cats, total = self.engine.breakdown(combats, self.selected, self.filters)
        t.insert("", "end", iid="__head", text=self.selected, tags=("cat",),
                 values=(fmt_num(total), f"{100 * total / total_all:.1f}" if total_all else "0",
                         "", f"{total / dur:,.1f}/s", "", "", ""))
        for cat in CAT_ORDER:
            if cat not in cats:
                continue
            s, abil = cats[cat]
            pct = 100 * s.total / total if total else 0
            acc = f"{s.acc:.0f}%" if s.misses or cat in ("melee", "ranged", "ws") else ""
            crit = f"{s.crit_rate:.0f}%" if cat in ("melee", "ranged") else ""
            node = t.insert("", "end", text=CAT_LABEL[cat], open=True, tags=("cat",),
                            values=(fmt_num(s.total), f"{pct:.1f}", s.hits, fmt_num(s.avg), fmt_num(s.max), acc, crit))
            if len(abil) > 1 or (abil and next(iter(abil)) not in (CAT_LABEL[cat], "Melee", "Ranged")):
                for name, a in sorted(abil.items(), key=lambda kv: -kv[1].total):
                    t.insert(node, "end", text=name, values=(
                        fmt_num(a.total), f"{100 * a.total / total:.1f}" if total else "0", a.hits,
                        fmt_num(a.avg), fmt_num(a.max), f"{a.acc:.0f}%" if a.misses else "", ""))

    def _draw_combats(self):
        t = self.cb_tree
        t.delete(*t.get_children())
        for c in reversed(self.engine.combats):
            rows, total, dur = self.engine.aggregate([c], self.filters)
            top = f"{rows[0].name} {rows[0].damage / dur:,.0f}" if rows else "-"
            t.insert("", "end", iid=str(c.id), text=f"#{c.id}", tags=("active",) if c.active else (),
                     values=(c.primary_mob(self.filters.mob_ok), fmt_time(dur), fmt_num(total),
                             f"{total / dur:,.1f}", top))
        if isinstance(self.view, int) and t.exists(str(self.view)):
            t.selection_set(str(self.view))

    def _draw_mobs(self, combats):
        t = self.mob_tree
        t.delete(*t.get_children())
        mobs = self.engine.mob_breakdown(combats, self.filters)
        total = sum(d for _, d in mobs) or 1
        for mob, d in mobs:
            if d > 0:
                t.insert("", "end", text=mob, values=(fmt_num(d), f"{100 * d / total:.1f}"))

    def _draw_sources(self):
        names = ["All characters"] + [s.name for s in self.engine.all_sources()]
        if list(self.src_box["values"]) != names:
            self.src_box["values"] = names

    def _draw_status(self):
        if self.s["compact"]:
            return
        now = time.time()
        parts = []
        for s in self.engine.all_sources():
            dot = "●" if s.online(now) else "○"
            extra = " ".join(x for x in (s.job, s.zone) if x)
            parts.append(f"{dot} {s.name}" + (f" ({extra})" if extra else ""))
        listen = f"listening {self.s['bind']}:{self.s['port']}" if not self.listen_err else f"PORT ERROR: {self.listen_err}"
        self.status.config(text=f"{listen}    " + ("   ".join(parts) if parts else "no characters connected — //lua load dpslink"))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
