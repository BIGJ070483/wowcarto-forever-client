"""Local annotation editor; source and coordinates are never changed by the form."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk


def local_fields(row):
    """Copy only editable/local fields while retaining location and opaque flags."""
    flags = row.get('flags', '')
    if not isinstance(flags, str):
        flags = json.dumps(flags, ensure_ascii=False, separators=(',', ':'))
    tags = row.get('tags')
    if not isinstance(tags, str):
        tags = '; '.join(row.get('property_names', []))
    return dict(name=row.get('name', ''), comment=row.get('comment', ''),
                category=row.get('category', 37), map_id=row['map_id'], x=row['x'], y=row['y'],
                min_level=row.get('min_level', 0), max_level=row.get('max_level', 0),
                tags=tags, flags=flags)


class AnnotationDialog(simpledialog.Dialog):
    def __init__(self, parent, initial, categories, translate, theme=None, editable_tags=True,
                 title='Annotation locale'):
        self.initial = local_fields(initial)
        self.categories = categories
        self.tr = translate
        self.theme = theme
        self.editable_tags = editable_tags
        self.fields = None
        super().__init__(parent, title=self.tr(title))

    def body(self, master):
        master.columnconfigure(1, weight=1)
        self.name = tk.StringVar(value=self.initial['name'])
        self.minimum = tk.StringVar(value=str(self.initial['min_level']))
        self.maximum = tk.StringVar(value=str(self.initial['max_level']))
        self.tags = tk.StringVar(value=self.initial['tags'])
        categories = sorted(self.categories.items(), key=lambda item: item[1]['name'].casefold())
        self.category_values = [(ident, f"{entry['name']} ({ident})") for ident, entry in categories]
        if not any(ident == self.initial['category'] for ident, _ in self.category_values):
            self.category_values.append((self.initial['category'], str(self.initial['category'])))
        selected = next(text for ident, text in self.category_values if ident == self.initial['category'])
        self.category = tk.StringVar(value=selected)
        for row, (label, variable) in enumerate((('Nom :', self.name), ('Niveau minimum :', self.minimum),
                                                 ('Niveau maximum :', self.maximum), ('Tags (séparés par ;) :', self.tags))):
            if label.startswith('Tags') and not self.editable_tags:
                continue
            tk.Label(master, text=self.tr(label), anchor='w').grid(row=row, column=0, sticky='w', padx=6, pady=3)
            entry = tk.Entry(master, textvariable=variable, width=48)
            entry.grid(row=row, column=1, sticky='ew', padx=6, pady=3)
            if row == 0: first = entry
        tk.Label(master, text=self.tr('Catégorie :'), anchor='w').grid(row=4, column=0, sticky='w', padx=6, pady=3)
        ttk.Combobox(master, textvariable=self.category, state='readonly',
                     values=[text for _, text in self.category_values]).grid(row=4, column=1, sticky='ew', padx=6, pady=3)
        tk.Label(master, text=self.tr('Commentaire :'), anchor='nw').grid(row=5, column=0, sticky='nw', padx=6, pady=3)
        self.comment = tk.Text(master, width=48, height=7, wrap='word')
        self.comment.grid(row=5, column=1, sticky='nsew', padx=6, pady=3)
        self.comment.insert('1.0', self.initial['comment'])
        return first

    def buttonbox(self):
        box = tk.Frame(self)
        tk.Button(box, text=self.tr('Enregistrer'), width=12, command=self.ok, default='active').pack(side='left', padx=5, pady=6)
        tk.Button(box, text=self.tr('Annuler'), width=12, command=self.cancel).pack(side='left', padx=5, pady=6)
        box.pack()
        def enter(event):
            if event.widget is self.comment:
                return 'break'
            self.ok(event)
            return 'break'
        self.bind('<Return>', enter)
        self.bind('<Escape>', self.cancel)
        if self.theme: self.theme()

    def validate(self):
        try:
            minimum, maximum = int(self.minimum.get()), int(self.maximum.get())
            if not 0 <= minimum <= 65535 or not 0 <= maximum <= 65535 or (minimum and maximum and minimum > maximum):
                raise ValueError(self.tr('La plage de niveaux est invalide.'))
            name = self.name.get()
            comment = self.comment.get('1.0', 'end-1c')
            tags = self.tags.get()
            if not name.strip() or len(name) > 200 or len(comment) > 65536 or len(tags) > 4096 or '\x00' in name+comment+tags:
                raise ValueError(self.tr('Le nom, le commentaire ou les tags sont invalides.'))
            category = next(ident for ident, label in self.category_values if label == self.category.get())
            self.fields = dict(self.initial, name=name, comment=comment, tags=tags,
                               min_level=minimum, max_level=maximum, category=category)
            return True
        except (ValueError, StopIteration):
            messagebox.showerror(self.tr('Annotation locale'), self.tr('Vérifiez le nom, les niveaux, la catégorie et les longueurs des textes.'), parent=self)
            return False

    def apply(self):
        self.result = self.fields
