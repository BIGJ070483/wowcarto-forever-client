"""Offline Windows map browser; data lives in separate versioned packs."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from tools.map_packs import MODES, checked_image, import_pack, load_pack

VERSION = '0.2.0'
UNAVAILABLE = {
    'Legacy': 'Aucun pack Legacy installé.\nRéférence 1.07 / 1.08b non disponible et fidélité non validée.\nLes cartes locales 1.20 sont accessibles dans Archives 1.20.',
    'Modern': 'Aucun pack Modern installé.\nLes stockages Era / Forever repérés ne constituent pas des cartes extraites et validées.\nLes cartes locales 1.20 sont accessibles dans Archives 1.20.',
    'Archives 1.20': 'Aucun pack d’archives installé. Importez un manifeste pack.json.',
}


class Viewer:
    def __init__(self, library, state_path=None):
        self.library = Path(library)
        self.state_path = Path(state_path) if state_path else self.library / 'preferences.json'
        self.root = tk.Tk()
        self.root.title('WoWCarto Forever — atlas local')
        self.root.geometry('1180x790')
        self.root.minsize(820, 560)
        self.packs, self.errors, self.entries, self.rows = [], [], [], {}
        self.current, self.image, self.photo = None, None, None
        self.scale, self.cx, self.cy = 1., 0., 0.
        self.drag_start, self.pending = None, None
        self.fit_on_resize = True
        self.loading = False
        self.mode = tk.StringVar(value='Archives 1.20')
        self.query = tk.StringVar()
        self.quality = tk.BooleanVar(value=True)
        self.caption = tk.StringVar(value='Choisissez une carte')
        self.provenance = tk.StringVar()
        self.status = tk.StringVar(value='Atlas hors ligne · fichiers originaux préservés')
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background='#f3efe5')
        style.configure('TLabel', background='#f3efe5', foreground='#272c2c')
        style.configure('Title.TLabel', font=('Segoe UI', 18, 'bold'))
        style.configure('Sub.TLabel', font=('Segoe UI', 10), foreground='#575e60')
        style.configure('Treeview', font=('Segoe UI', 10), rowheight=27)
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill='both', expand=True)
        top = ttk.Frame(outer)
        top.pack(fill='x')
        ttk.Label(top, text='WoWCarto Forever', style='Title.TLabel').pack(side='left')
        ttk.Label(top, text='ATLAS LOCAL  /  0.2', style='Sub.TLabel').pack(side='left', padx=18)
        ttk.Button(top, text='Importer un pack…', command=self.import_dialog).pack(side='right')
        controls = ttk.Frame(outer, padding=(0, 14, 0, 12))
        controls.pack(fill='x')
        ttk.Label(controls, text='Mode').pack(side='left')
        self.mode_box = ttk.Combobox(controls, textvariable=self.mode, values=MODES, state='readonly', width=19)
        self.mode_box.pack(side='left', padx=(8, 20))
        self.mode_box.bind('<<ComboboxSelected>>', lambda event: self.refresh_entries())
        ttk.Checkbutton(controls, text='Haute résolution', variable=self.quality, command=self.load_selected).pack(side='left')
        ttk.Button(controls, text='Sources et limites', command=self.show_sources).pack(side='right')
        body = ttk.Frame(outer)
        body.pack(fill='both', expand=True)
        sidebar = ttk.Frame(body, width=255)
        sidebar.pack(side='left', fill='y', padx=(0, 14))
        ttk.Label(sidebar, text='CARTES DISPONIBLES', style='Sub.TLabel').pack(anchor='w', pady=(0, 8))
        self.search = ttk.Entry(sidebar, textvariable=self.query, width=30)
        self.search.pack(fill='x', pady=(0, 8))
        self.query.trace_add('write', lambda *_: self.refresh_entries())
        treeframe = ttk.Frame(sidebar)
        treeframe.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(treeframe, show='tree', selectmode='browse', height=16)
        scroll = ttk.Scrollbar(treeframe, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda event: self.load_selected())
        self.count = ttk.Label(sidebar, text='', style='Sub.TLabel')
        self.count.pack(anchor='w', pady=8)
        content = ttk.Frame(body)
        content.pack(side='left', fill='both', expand=True)
        ttk.Label(content, textvariable=self.caption, font=('Segoe UI', 13, 'bold')).pack(anchor='w')
        self.source_label = ttk.Label(content, textvariable=self.provenance, style='Sub.TLabel', wraplength=780)
        self.source_label.pack(anchor='w', pady=(4, 10))
        content.bind('<Configure>', lambda e: self.source_label.configure(wraplength=max(220, e.width - 4)))
        self.canvas = tk.Canvas(content, bg='#1d292b', highlightthickness=0, takefocus=True, cursor='fleur')
        self.canvas.pack(fill='both', expand=True)
        bottom = ttk.Frame(content, padding=(0, 9, 0, 0))
        bottom.pack(fill='x')
        for text, command in [('−', lambda: self.zoom(1 / 1.4)), ('+', lambda: self.zoom(1.4)),
                              ('Adapter', self.fit), ('100 %', self.actual_size),
                              ('Précédente', lambda: self.next_map(-1)), ('Suivante', lambda: self.next_map(1))]:
            ttk.Button(bottom, text=text, command=command).pack(side='left', padx=(0, 5))
        self.zoom_label = ttk.Label(bottom, text='')
        self.zoom_label.pack(side='right')
        ttk.Label(outer, textvariable=self.status, style='Sub.TLabel', wraplength=1120).pack(anchor='w', pady=(12, 0))
        ttk.Label(outer, text='Glisser pour déplacer · molette pour zoomer · flèches pour parcourir · Ctrl+F pour rechercher', style='Sub.TLabel').pack(anchor='w', pady=(4, 0))
        self.canvas.bind('<Configure>', self.resize)
        self.canvas.bind('<ButtonPress-1>', self.pan_start)
        self.canvas.bind('<B1-Motion>', self.pan_move)
        self.canvas.bind('<MouseWheel>', lambda e: self.zoom(1.25 if e.delta > 0 else .8, e.x, e.y))
        self.canvas.bind('<Button-4>', lambda e: self.zoom(1.25, e.x, e.y))
        self.canvas.bind('<Button-5>', lambda e: self.zoom(.8, e.x, e.y))
        for key, delta in [('Left', (100, 0)), ('Right', (-100, 0)), ('Up', (0, 100)), ('Down', (0, -100))]:
            self.canvas.bind('<' + key + '>', lambda e, d=delta: self.pan(*d))
        self.canvas.bind('<Home>', lambda e: self.fit())
        self.root.bind('<Control-f>', lambda e: self.search.focus_set())
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.read_packs()
        self.restore_preferences()
        self.refresh_entries()

    def read_packs(self):
        self.packs, self.errors = [], []
        if self.library.is_dir():
            for manifest in sorted(self.library.glob('*/pack.json')):
                try:
                    if (manifest.parent / 'INCOMPLETE').exists():
                        raise ValueError('Import incomplet, pack désactivé.')
                    self.packs.append((manifest, load_pack(manifest)))
                except (OSError, ValueError, KeyError, TypeError) as error:
                    self.errors.append(manifest.parent.name + ' : ' + str(error))

    def restore_preferences(self):
        self.saved = {}
        try:
            if self.state_path.stat().st_size < 100000:
                data = json.loads(self.state_path.read_text(encoding='utf-8'))
                if data.get('mode') in MODES:
                    self.mode.set(data['mode'])
                self.saved = data
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def close(self):
        data = dict(mode=self.mode.get())
        if self.current:
            manifest, pack, row = self.current
            data.update(pack=pack['id'], map=row['id'], high=self.quality.get())
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix('.tmp')
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            temp.replace(self.state_path)
        except OSError:
            pass
        self.root.destroy()

    def refresh_entries(self):
        previous = (self.current[1]['id'], self.current[2]['id']) if self.current else None
        self.loading = True
        self.tree.delete(*self.tree.get_children())
        self.rows, self.entries = {}, []
        groups = {}
        for manifest, pack in self.packs:
            if pack['mode'] != self.mode.get():
                continue
            for row in pack['maps']:
                if self.query.get().casefold() not in (row['title'] + ' ' + row['group']).casefold():
                    continue
                group = (pack['id'], row['group'])
                if group not in groups:
                    groups[group] = self.tree.insert('', 'end', text=row['group'] or pack['title'], open=True)
                iid = self.tree.insert(groups[group], 'end', text=row['title'])
                self.rows[iid] = (manifest, pack, row)
                self.entries.append(iid)
        self.loading = False
        self.count.configure(text=f'{len(self.entries)} carte(s) · {len(self.packs)} pack(s) installé(s)')
        self.current, self.image = None, None
        self.provenance.set('')
        if self.entries:
            wanted = previous or (self.saved.get('pack'), self.saved.get('map'))
            selected = next((iid for iid, (_, p, r) in self.rows.items() if (p['id'], r['id']) == wanted), self.entries[0])
            self.saved = {}
            self.tree.selection_set(selected)
            self.tree.see(selected)
            self.load_selected()
        else:
            self.caption.set(self.mode.get() + ' — aucune carte')
            self.status.set('Aucun résultat pour cette recherche.' if self.query.get() else UNAVAILABLE[self.mode.get()].replace('\n', ' '))
            self.draw()

    def load_selected(self):
        if self.loading:
            return
        selection = self.tree.selection()
        if not selection or selection[0] not in self.rows:
            return
        self.current = self.rows[selection[0]]
        manifest, pack, row = self.current
        self.caption.set(row['title'])
        self.provenance.set(pack['title'] + ' · ' + pack['version'])
        self.image = None
        kind = 'high' if self.quality.get() and row.get('high') else 'standard'
        try:
            self.image = checked_image(manifest.parent, row[kind])
            resolution = 'HD' if kind == 'high' else 'standard'
            note = ' · HD absente : résolution standard explicitement affichée' if self.quality.get() and kind == 'standard' else ''
            self.status.set(f'{self.image.width} × {self.image.height} · {resolution}{note} · {pack["limitations"]}')
            self.fit()
        except (OSError, ValueError) as error:
            self.status.set(str(error))
            self.draw()

    def dimensions(self):
        return max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())

    def fit(self):
        if self.image:
            w, h = self.dimensions()
            self.scale = min((w - 30) / self.image.width, (h - 30) / self.image.height)
            self.scale = max(.02, min(16., self.scale))
            self.cx, self.cy = self.image.width / 2, self.image.height / 2
            self.fit_on_resize = True
        self.draw()

    def actual_size(self):
        self.scale, self.fit_on_resize = 1., False
        self.draw()

    def zoom(self, factor, x=None, y=None):
        if not self.image:
            return
        w, h = self.dimensions()
        x, y = (w / 2 if x is None else x), (h / 2 if y is None else y)
        previous = self.scale
        self.scale = max(.02, min(16., self.scale * factor))
        self.cx += (x - w / 2) * (1 / previous - 1 / self.scale)
        self.cy += (y - h / 2) * (1 / previous - 1 / self.scale)
        self.fit_on_resize = False
        self.draw()

    def pan_start(self, event):
        self.canvas.focus_set()
        self.drag_start = event.x, event.y, self.cx, self.cy

    def pan_move(self, event):
        if self.drag_start and self.image:
            x, y, cx, cy = self.drag_start
            self.cx = cx - (event.x - x) / self.scale
            self.cy = cy - (event.y - y) / self.scale
            self.fit_on_resize = False
            self.draw()

    def pan(self, dx, dy):
        if self.image:
            self.cx -= dx / self.scale
            self.cy -= dy / self.scale
            self.fit_on_resize = False
            self.draw()

    def resize(self, event):
        if self.pending:
            self.root.after_cancel(self.pending)
        self.pending = self.root.after(60, self.resized)

    def resized(self):
        self.pending = None
        if self.fit_on_resize:
            self.fit()
        else:
            self.draw()

    def draw(self):
        self.canvas.delete('all')
        w, h = self.dimensions()
        if not self.image:
            text = self.status.get() if self.current else ('Aucune carte pour cette recherche.' if self.query.get() else UNAVAILABLE[self.mode.get()])
            self.canvas.create_text(w / 2, h / 2, text=text, fill='#e5ded0', font=('Segoe UI', 13), width=max(220, w - 100), justify='center')
            self.zoom_label.configure(text='')
            self.photo = None
            return
        self.cx = max(0., min(float(self.image.width), self.cx))
        self.cy = max(0., min(float(self.image.height), self.cy))
        left, top = w / 2 - self.cx * self.scale, h / 2 - self.cy * self.scale
        x0, y0 = max(0, math.floor(-left / self.scale)), max(0, math.floor(-top / self.scale))
        x1 = min(self.image.width, math.ceil((w - left) / self.scale))
        y1 = min(self.image.height, math.ceil((h - top) / self.scale))
        if x1 > x0 and y1 > y0:
            region = self.image.crop((x0, y0, x1, y1))
            size = max(1, round(region.width * self.scale)), max(1, round(region.height * self.scale))
            region = region.resize(size, Image.Resampling.LANCZOS if self.scale < 1 else Image.Resampling.BILINEAR)
            self.photo = ImageTk.PhotoImage(region)
            self.canvas.create_image(round(left + x0 * self.scale), round(top + y0 * self.scale), image=self.photo, anchor='nw')
        self.zoom_label.configure(text=f'{self.scale * 100:.0f} %')

    def next_map(self, delta):
        if self.entries:
            current = self.tree.selection()
            index = self.entries.index(current[0]) if current and current[0] in self.entries else 0
            iid = self.entries[(index + delta) % len(self.entries)]
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def show_sources(self):
        lines = []
        for manifest, pack in self.packs:
            lines.append(f'{pack["title"]} [{pack["mode"]}]\nVersion : {pack["version"]}\nProvenance : {pack["provenance"]}\nLimites : {pack["limitations"]}\n{len(pack["maps"])} cartes · {manifest}')
        if self.errors:
            lines.append('Packs refusés :\n' + '\n'.join(self.errors))
        dialog = tk.Toplevel(self.root)
        dialog.title('Sources et limites')
        dialog.geometry('780x530')
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill='both', expand=True)
        scroll = ttk.Scrollbar(frame)
        text = tk.Text(frame, wrap='word', font=('Segoe UI', 10), padx=12, pady=10, yscrollcommand=scroll.set)
        scroll.configure(command=text.yview)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        text.insert('1.0', '\n\n'.join(lines) or 'Aucun pack installé.')
        text.configure(state='disabled')
        ttk.Button(dialog, text='Fermer', command=dialog.destroy).pack(pady=(0, 12))

    def import_dialog(self):
        selected = filedialog.askopenfilename(title='Importer un pack de cartes', filetypes=[('Manifeste de pack', 'pack.json')], parent=self.root)
        if not selected:
            return
        try:
            result = import_pack(selected, self.library)
            data = load_pack(result)
            self.read_packs()
            self.mode.set(data['mode'])
            self.query.set('')
            self.refresh_entries()
        except (OSError, ValueError, KeyError, TypeError) as error:
            messagebox.showerror('Import refusé', str(error), parent=self.root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    home = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    parser.add_argument('--library', type=Path, default=home / 'packs')
    parser.add_argument('--state', type=Path)
    args = parser.parse_args()
    viewer = Viewer(args.library, args.state)
    viewer.root.mainloop()


if __name__ == '__main__':
    main()
