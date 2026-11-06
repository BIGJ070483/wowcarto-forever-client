"""Integrated Windows client built from the existing map and database components."""
from __future__ import annotations
import argparse
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import uuid

from PIL import Image, ImageTk
from tools.clone_map import MapCanvas
from tools.clone_annotation_dialog import AnnotationDialog
from tools.clone_i18n import translate
from tools.clone_model import Model, folded
from tools.clone_regions import (FOREVER_REGISTRATION, decode_region_outline, polygon_bounds,
                                 project_forever, project_historical_records)
from tools.clone_store import Store
from tools.clone_community import CommunityClient
from tools.clone_reference_community import ReferenceCommunityClient, SOURCES, submit_reference
from tools.clone_contributions import MAX_FILE, submit_additions, submit_correction_fields, personal_addition
from tools.community_transport import SNAPSHOT_URL
from urllib.error import HTTPError
from tools.native_additions import parse_additions
from tools.map_packs import checked_image, load_pack, local_file


def restore_community_caches(model, community, reference_community):
    """Restore verified caches without network access; keep invalid files intact."""
    errors = []
    try:
        model.community = community.cached(SNAPSHOT_URL)['annotations']
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        errors.append('community')
    for locale in SOURCES:
        try:
            model.reference_corrections[locale] = {r['id']: r for r in reference_community.cached(locale)}
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            errors.append(locale)
    return errors


class Application:
    def __init__(self, data, state):
        self.data = Path(data)
        self.preferences_path = Path(state).with_name('client-preferences.json')
        self.preferences = self.load_preferences()
        self.store = Store(state)
        self.community = CommunityClient(Path(state).with_name('community-cache.json'))
        self.model = Model(self.data, self.store)
        self.reference_community = ReferenceCommunityClient(Path(state).parent)
        cache_errors = restore_community_caches(self.model, self.community, self.reference_community)
        self.languages = self.model.available_languages()
        self.root = tk.Tk()
        self.root.title('WoWCarto Forever — client intégré en validation')
        self.root.geometry('852x735')
        self.root.minsize(800, 550)
        self.mode = tk.StringVar(value='Référence 1.20')
        self.database = tk.StringVar(value=self.preferences.get('database')
                                     if self.preferences.get('database') in self.languages else 'Francais')
        self.edit_enabled = tk.BooleanVar(value=False)
        self.edit_database = tk.StringVar(value=self.preferences.get('edit_database')
                                          if self.preferences.get('edit_database') in self.languages else 'Francais')
        self.ui_language = tk.StringVar(value=self.preferences.get('ui_language')
                                        if self.preferences.get('ui_language') in ('fr', 'en') else 'fr')
        self.dark_theme = tk.BooleanVar(value=self.preferences.get('theme') == 'dark')
        self.query = tk.StringVar()
        self.status = tk.StringVar(value='')
        self.label = tk.StringVar(value='')
        self.selection = None
        self.groups = {}
        self.results = {}
        self.tiles = []
        self.shown_group = None
        self.root.option_add('*Font', ('MS Sans Serif', 8))
        self.load_interface_icons()
        bar = tk.Frame(self.root, height=25, bd=1, relief='raised')
        bar.pack(fill='x'); bar.pack_propagate(False)
        self.flag = tk.Menubutton(bar, text='🇫🇷', relief='flat', width=3)
        self.flag.pack(side='left')
        choices = tk.Menu(self.flag, tearoff=False); self.flag['menu'] = choices
        for label in ('Référence 1.20', 'Legacy', 'Modern'):
            choices.add_radiobutton(label=label, variable=self.mode, value=label, command=self.change_mode)
        choices.add_separator()
        for value in self.languages:
            label = {'Francais': 'FR', 'US': 'EN'}.get(value, value.upper())
            choices.add_radiobutton(label='Base : ' + label, variable=self.database, value=value, command=self.change_database)
        choices.add_separator()
        choices.add_checkbutton(label='Thème sombre', variable=self.dark_theme, command=self.change_theme)
        language_menu = tk.Menu(choices, tearoff=False)
        choices.add_cascade(label='Langue de l’interface', menu=language_menu)
        language_menu.add_radiobutton(label='Français', variable=self.ui_language, value='fr', command=self.change_ui_language)
        language_menu.add_radiobutton(label='English', variable=self.ui_language, value='en', command=self.change_ui_language)
        choices.add_checkbutton(label='Mode édition', variable=self.edit_enabled, command=self.edit_mode_changed)
        edit_choices = tk.Menu(choices, tearoff=False)
        choices.add_cascade(label='Base autorisée pour l’édition', menu=edit_choices)
        for value in self.languages:
            label = {'Francais': 'FR', 'US': 'EN'}.get(value, value.upper())
            edit_choices.add_radiobutton(label=label, variable=self.edit_database, value=value, command=self.edit_database_changed)
        choices.add_separator()
        choices.add_command(label='Importer mes annotations…', command=self.import_notes)
        choices.add_command(label='Exporter mes annotations…', command=self.export_notes)
        choices.add_command(label='Restaurer une annotation masquée…', command=self.restore_local_note)
        choices.add_command(label='Partager un fichier d’ajouts…', command=self.share_additions)
        choices.add_command(label='Synchroniser', command=self.sync)
        choices.add_command(label='Plans Legacy…', command=self.show_legacy_plans)
        for index, hint, fn in [(15, 'Garder au premier plan', self.toggle_topmost),
                                (26, 'Mise à jour', self.sync),
                                (22, 'Informations sur la carte', self.show_mode_info),
                                (25, 'Génération des cartes', self.show_mode_info),
                                (27, 'Options', self.show_mode_info),
                                (7, 'Informations', self.show_mode_info),
                                (24, 'Mode édition', self.toggle_edit_mode)]:
            self.icon_button(bar, index, hint, fn).pack(side='left')
        for index, hint, fn in reversed([(2, 'Régions', lambda: self.show_pane('regions')),
                                          (3, 'Annotations', lambda: self.show_pane('results')),
                                          (4, 'Recherche', self.show_search),
                                          (5, 'Détails', lambda: self.show_pane('details'))]):
            self.icon_button(bar, index, hint, fn).pack(side='right')
        body = tk.Frame(self.root); body.pack(fill='both', expand=True)
        side = tk.Frame(body, width=25, bd=1, relief='raised'); side.pack(side='left', fill='y'); side.pack_propagate(False)
        for index, hint, fn in [(0, 'Légendes', self.toggle_legend),
                                (1, 'Chemins', self.not_implemented),
                                (16, 'Carte générale', lambda: self.map.fit()),
                                (17, 'Assombrir la carte', self.toggle_dim),
                                (19, 'Rechercher', self.show_search),
                                (6, 'Historique des recherches', self.show_search)]:
            self.icon_button(side, index, hint, fn).pack(side='top', pady=1)
        right = tk.Frame(body, width=251, bd=1, relief='sunken'); right.pack(side='right', fill='y'); right.pack_propagate(False)
        center = tk.Frame(body, bd=1, relief='sunken'); center.pack(side='left', fill='both', expand=True)
        self.map = MapCanvas(center, on_select=self.select_record, on_context=self.context,
                             on_view=self.view_changed, status=self.status.set, translate=self.tr)
        self.map.on_region_select = self.select_region_marker
        self.map.pack(fill='both', expand=True)
        minirow = tk.Frame(right, height=108); minirow.pack(fill='x'); minirow.pack_propagate(False)
        self.mini = tk.Canvas(minirow, width=100, height=100, bg='#00242c', highlightthickness=1)
        self.mini.place(x=10, y=2)
        self.mini.bind('<Button-1>', self.mini_click)
        tk.Label(minirow, text='Niveau du Perso').place(x=118, y=53)
        self.level = tk.StringVar(value='5')
        tk.Entry(minirow, textvariable=self.level, width=3).place(x=216, y=50)
        self.zoom = tk.Scale(minirow, from_=0, to=100, showvalue=False, orient='horizontal', length=120,
                             command=self.zoom_slider)
        self.zoom.place(x=116, y=72)
        self.panes = tk.Frame(right); self.panes.pack(fill='both', expand=True)
        self.region_pane = tk.Frame(self.panes)
        filterbar = tk.Frame(self.region_pane, height=27); filterbar.pack(fill='x'); filterbar.pack_propagate(False)
        tk.Label(filterbar, text='Filtre (F5):').pack(side='left', padx=4)
        self.region_filter = tk.StringVar(); self.region_filter.trace_add('write', self.filter_regions)
        self.filter_entry = tk.Entry(filterbar, textvariable=self.region_filter); self.filter_entry.pack(side='left', fill='x', expand=True)
        tk.Button(filterbar, text='×', bd=0, command=lambda: self.region_filter.set('')).pack(side='right')
        continentbar = tk.Frame(self.region_pane); continentbar.pack(fill='x')
        tk.Label(continentbar, text='Continent :').pack(side='left', padx=4)
        self.continent_filter = tk.StringVar(value='Tous')
        self.continent_box = ttk.Combobox(continentbar, textvariable=self.continent_filter, state='readonly', width=20)
        self.continent_box.pack(side='left', fill='x', expand=True)
        self.continent_box.bind('<<ComboboxSelected>>', self.filter_regions)
        style = ttk.Style(self.root)
        style.configure('Original.Treeview', background='#808080', fieldbackground='#808080', foreground='white', rowheight=18)
        self.regions = ttk.Treeview(self.region_pane, show='tree', selectmode='browse', style='Original.Treeview')
        self.regions.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(self.region_pane, command=self.regions.yview); scroll.pack(side='right', fill='y'); self.regions.configure(yscrollcommand=scroll.set)
        self.regions.bind('<<TreeviewSelect>>', self.select_group)
        self.result_window = tk.Toplevel(self.root); self.result_window.title('Résultats des annotations')
        self.result_window.geometry('420x480'); self.result_window.withdraw()
        self.result_window.protocol('WM_DELETE_WINDOW', self.result_window.withdraw)
        results_body = tk.Frame(self.result_window); results_body.pack(fill='both', expand=True)
        self.results_tree = ttk.Treeview(results_body, show='tree')
        result_scroll = ttk.Scrollbar(results_body, command=self.results_tree.yview)
        result_scroll.pack(side='right', fill='y')
        self.results_tree.configure(yscrollcommand=result_scroll.set)
        self.results_tree.pack(side='left', fill='both', expand=True)
        self.results_tree.bind('<<TreeviewSelect>>', self.select_result)
        result_nav = tk.Frame(self.result_window); result_nav.pack(fill='x')
        self.results_page = tk.StringVar(value='')
        self.previous_results = tk.Button(result_nav, text='Précédent', command=lambda: self.move_results(-1), state='disabled')
        self.previous_results.pack(side='left')
        tk.Label(result_nav, textvariable=self.results_page).pack(side='left', padx=8)
        self.next_results = tk.Button(result_nav, text='Suivant', command=lambda: self.move_results(1), state='disabled')
        self.next_results.pack(side='right')
        self.search_matches, self.results_offset = [], 0
        self.detail_window = tk.Toplevel(self.root); self.detail_window.title('Détails de l’annotation')
        self.detail_window.geometry('400x300'); self.detail_window.withdraw()
        self.detail_window.protocol('WM_DELETE_WINDOW', self.detail_window.withdraw)
        self.detail = tk.Text(self.detail_window, wrap='word', font=('MS Sans Serif', 8))
        self.detail.pack(fill='both', expand=True)
        self.show_pane('regions')
        tk.Label(self.root, textvariable=self.status, anchor='w', bd=1, relief='sunken').pack(fill='x', side='bottom')
        self.root.bind('<Control-f>', lambda e: self.show_search())
        self.root.bind('<F5>', lambda e: self.filter_entry.focus_set())
        self.root.bind('<Control-s>', lambda e: self.status.set(self.tr('Annotations locales enregistrées automatiquement.')))
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.apply_ui_language()
        if self.dark_theme.get():
            self.apply_theme()
        self.change_mode()
        self.root.after(250, self.initial_view)
        if cache_errors:
            self.root.after(500, lambda: self.status.set(self.tr('Cache communautaire illisible : fichier conservé.')))

    def initial_view(self):
        self.map.fit()
        if self.shown_region in self.region_outlines:
            self.map.fit_bounds(polygon_bounds(self.region_outlines[self.shown_region]))
        elif self.mode.get() == 'Référence 1.20':
            point = self.region_centers.get(self.shown_region)
            if point:
                self.map.center(point[0], point[1], .8)

    def load_interface_icons(self):
        self.toolbar_icons = {}
        self.toolbar_hot_icons = {}
        for name, output in (('icon_a.bmp', self.toolbar_icons), ('icon_b.bmp', self.toolbar_hot_icons)):
            path = self.data / 'interface-originale' / name
            if not path.is_file():
                continue
            with Image.open(path) as source:
                strip = source.convert('RGBA')
            mask = strip.getpixel((0, 0))[:3]
            for i in range(strip.width // 16):
                icon = strip.crop((i * 16, 0, (i + 1) * 16, 16))
                icon.putdata([(r, g, b, 0 if (r, g, b) == mask else 255) for r, g, b, a in icon.getdata()])
                output[i] = ImageTk.PhotoImage(icon)

    def load_preferences(self):
        try:
            content = json.loads(self.preferences_path.read_text(encoding='utf-8'))
            if not isinstance(content, dict):
                raise ValueError('Préférences invalides')
            return content
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # Preserve an unreadable file for recovery; do not overwrite it.
            self.preferences_read_error = True
            return {}

    def save_preferences(self):
        if getattr(self, 'preferences_read_error', False):
            self.status.set('Préférences illisibles : fichier conservé sans modification.')
            return
        temp_path = None
        try:
            self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.preferences_path.parent,
                                             prefix='.wcf-preferences-', suffix='.tmp', delete=False) as stream:
                temp_path = Path(stream.name)
                json.dump(self.preferences, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.preferences_path)
        except OSError as error:
            self.status.set('Préférences non enregistrées : ' + str(error))
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def change_theme(self):
        self.apply_theme()
        self.preferences['theme'] = 'dark' if self.dark_theme.get() else 'light'
        self.save_preferences()

    def change_ui_language(self):
        self.preferences['ui_language'] = self.ui_language.get()
        self.save_preferences()
        self.apply_ui_language()
        self.root.title(self.tr('WoWCarto Forever — Modern : base historique 1.20' if self.mode.get() == 'Modern'
                                else 'WoWCarto Forever — client intégré en validation'))
        values = self.continent_box.cget('values')
        if values:
            self.continent_box['values'] = (('All' if self.ui_language.get() == 'en' else 'Tous'), *values[1:])
        if self.continent_filter.get() in ('Tous', 'All'):
            self.continent_filter.set('All' if self.ui_language.get() == 'en' else 'Tous')
        # Old status text describes the previous view and may be in another language.
        self.status.set('')
        self.map.schedule()

    def tr(self, text):
        return translate(text, self.ui_language.get())

    def apply_ui_language(self):
        """Translate interface controls while leaving user and map data untouched."""
        language = self.ui_language.get()
        def visit(widget):
            if isinstance(widget, tk.Menu):
                if not hasattr(widget, '_wcf_labels'):
                    widget._wcf_labels = {}
                end = widget.index('end')
                if end is not None:
                    for index in range(end + 1):
                        try:
                            original = widget._wcf_labels.setdefault(index, widget.entrycget(index, 'label'))
                            widget.entryconfigure(index, label=translate(original, language))
                        except tk.TclError:
                            pass
            elif not isinstance(widget, (ttk.Widget, tk.Canvas, tk.Text, tk.Entry, tk.Listbox)):
                try:
                    if not hasattr(widget, '_wcf_text'):
                        widget._wcf_text = widget.cget('text')
                    if widget._wcf_text:
                        widget.configure(text=translate(widget._wcf_text, language))
                except tk.TclError:
                    pass
            if isinstance(widget, tk.Toplevel):
                if not hasattr(widget, '_wcf_title'):
                    widget._wcf_title = widget.title()
                widget.title(translate(widget._wcf_title, language))
            for child in widget.winfo_children():
                visit(child)
        visit(self.root)
        self.flag.configure(text='🇬🇧' if language == 'en' else '🇫🇷')

    def change_database(self):
        self.change_mode()
        self.preferences['database'] = self.database.get()
        self.save_preferences()

    def edit_mode_changed(self):
        self.status.set(translate('Mode édition activé.' if self.edit_enabled.get() else 'Mode édition désactivé.', self.ui_language.get()))

    def toggle_edit_mode(self):
        self.edit_enabled.set(not self.edit_enabled.get())
        self.edit_mode_changed()

    def edit_database_changed(self):
        self.preferences['edit_database'] = self.edit_database.get()
        self.save_preferences()
        self.status.set(self.tr('Base autorisée pour l’édition : ') + self.edit_database.get())

    def can_edit(self, allow_modern=False):
        if self.mode.get() != 'Référence 1.20' and not (allow_modern and self.mode.get() == 'Modern'):
            self.status.set(translate('L’édition locale est disponible en référence 1.20.', self.ui_language.get()))
            return False
        if not self.edit_enabled.get():
            self.status.set(translate('Activez le mode édition pour modifier des annotations.', self.ui_language.get()))
            return False
        if self.database.get() != self.edit_database.get():
            self.status.set(translate('Cette base ne correspond pas à la base autorisée pour l’édition.', self.ui_language.get()))
            return False
        return True

    def apply_theme(self):
        """Switch existing controls without altering the map or source resources."""
        dark = self.dark_theme.get()
        palette = {'background': '#252930', 'foreground': '#edf0f4',
                   'activebackground': '#414a56', 'activeforeground': '#ffffff',
                   'selectbackground': '#344d67', 'selectforeground': '#ffffff',
                   'insertbackground': '#ffffff'}
        if not hasattr(self, '_theme_defaults'):
            self._theme_defaults = {}
        def visit(widget):
            if not isinstance(widget, (tk.Canvas, ttk.Widget)):
                original = self._theme_defaults.setdefault(str(widget), {})
                colors = dict(palette)
                if isinstance(widget, (tk.Checkbutton, tk.Radiobutton)):
                    colors['selectcolor'] = '#343a43'
                for key, color in colors.items():
                    try:
                        if key not in original:
                            original[key] = widget.cget(key)
                        widget.configure(**{key: color if dark else original[key]})
                    except tk.TclError:
                        pass
            for child in widget.winfo_children():
                visit(child)
        visit(self.root)
        style = ttk.Style(self.root)
        if not hasattr(self, '_original_ttk_theme'):
            self._original_ttk_theme = style.theme_use()
        style.theme_use('clam' if dark else self._original_ttk_theme)
        style.configure('Original.Treeview', background='#252930' if dark else '#808080',
                        fieldbackground='#252930' if dark else '#808080', foreground='white')
        style.configure('Treeview', background='#252930' if dark else 'white',
                        fieldbackground='#252930' if dark else 'white',
                        foreground='#edf0f4' if dark else 'black')
        if dark:
            style.configure('TCombobox', fieldbackground='#343a43', background='#343a43', foreground='#edf0f4', arrowcolor='#edf0f4')
            style.map('TCombobox', fieldbackground=[('readonly', '#343a43')], foreground=[('readonly', '#edf0f4')])
            style.configure('TScrollbar', background='#414a56', troughcolor='#252930', arrowcolor='#edf0f4')
        else:
            style.configure('TCombobox', fieldbackground='', background='', foreground='', arrowcolor='')
            style.map('TCombobox', fieldbackground=[], foreground=[])
            style.configure('TScrollbar', background='', troughcolor='', arrowcolor='')

    def icon_button(self, parent, index, hint, command):
        icon = self.toolbar_icons.get(index)
        hot_icon = self.toolbar_hot_icons.get(index)
        button = tk.Button(parent, image=icon, text='' if icon else hint[:1], command=command,
                           width=22 if icon else 2, height=22 if icon else 1,
                           bd=0, relief='flat', takefocus=False)
        def enter(_):
            self.status.set(translate(hint, self.ui_language.get()))
            if hot_icon:
                button.configure(image=hot_icon)
        def leave(_):
            if icon:
                button.configure(image=icon)
        button.bind('<Enter>', enter)
        button.bind('<Leave>', leave)
        return button

    def show_pane(self, name):
        self.region_pane.pack(fill='both', expand=True)
        if name in ('results', 'details'):
            window = self.result_window if name == 'results' else self.detail_window
            window.deiconify(); window.lift()

    def filter_regions(self, *_):
        if not hasattr(self, 'regions'):
            return
        text = folded(self.region_filter.get().strip())
        continent = self.continent_filter.get()
        for iid in getattr(self, 'region_ids', []):
            if not self.regions.exists(iid):
                continue
            if text in folded(self.regions.item(iid, 'text')) and (continent in ('Tous', 'All') or self.region_continents.get(iid) == continent):
                self.regions.move(iid, '', 'end')
            else:
                self.regions.detach(iid)

    def toggle_topmost(self):
        self.root.attributes('-topmost', not bool(self.root.attributes('-topmost')))

    def toggle_legend(self):
        self.map.legend = not self.map.legend
        self.map.schedule()

    def toggle_dim(self):
        self.map.dim = not self.map.dim
        self.map.schedule()

    def zoom_slider(self, value):
        if hasattr(self, 'map'):
            self.map.zoom_to(.025 * (320 ** (float(value) / 100)))

    def not_implemented(self):
        self.status.set(self.tr('Fonction non encore intégrée dans ce client de validation.'))

    def show_mode_info(self):
        messagebox.showinfo('WoWCarto Forever', self.label.get(), parent=self.root)

    def load_category_icons(self):
        self.map.icons = {}
        strip = Image.open(io.BytesIO(self.model.db['icons_bmp'])).convert('RGBA')
        mask = strip.getpixel((0, 0))[:3]
        for ident, category in self.model.categories.items():
            index = category.get('icon_index', -1)
            if 0 <= index < strip.width // 16:
                icon = strip.crop((index * 16, 0, (index + 1) * 16, 16))
                icon.putdata([(r, g, b, 0 if (r, g, b) == mask else 255) for r, g, b, a in icon.getdata()])
                self.map.icons[ident] = ImageTk.PhotoImage(icon)
        self.map.region_icon = self.map.icons.get(18)

    def show_search(self):
        if self.mode.get() not in ('Référence 1.20', 'Modern') and not (self.mode.get() == 'Legacy' and self.model.map_set.get('id') == 'legacy108'):
            self.status.set(self.tr('Aucune base d’annotations calibrée pour ce mode.'))
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('Recherche')
        x = max(0, (dialog.winfo_screenwidth() - 540) // 2)
        y = max(0, (dialog.winfo_screenheight() - 450) // 2)
        dialog.geometry(f'540x450+{x}+{y}')
        dialog.transient(self.root)
        left = tk.Frame(dialog, padx=5, pady=5); left.pack(side='left', fill='both', expand=True)
        right = tk.Frame(dialog, padx=5, pady=5); right.pack(side='right', fill='both', expand=True)
        options = tk.Frame(left); options.pack(fill='x')
        tk.Label(options, text='Chercher dans:').pack(side='left')
        names = tk.BooleanVar(value=True); comments = tk.BooleanVar(value=False)
        tk.Checkbutton(options, text='Nom', variable=names).pack(side='left')
        tk.Checkbutton(options, text='Commentaires', variable=comments).pack(side='left')
        entry = tk.Entry(left, textvariable=self.query); entry.pack(fill='x', pady=3); entry.focus_set()
        levelrow = tk.Frame(left); levelrow.pack(fill='x')
        use_level = tk.BooleanVar(value=False); low = tk.StringVar(value='5'); high = tk.StringVar(value='5')
        tk.Checkbutton(levelrow, text='Niveau [min/max]', variable=use_level).pack(side='left')
        tk.Entry(levelrow, textvariable=low, width=5).pack(side='left', padx=3)
        tk.Entry(levelrow, textvariable=high, width=5).pack(side='left')
        tk.Label(left, text='Filtre').pack(anchor='w')
        filter_text = tk.StringVar(); filter_entry = tk.Entry(left, textvariable=filter_text); filter_entry.pack(fill='x')
        category_tree = ttk.Treeview(left, show='tree', selectmode='extended', height=11)
        category_tree.pack(fill='both', expand=True, pady=4)
        roots = {kind: category_tree.insert('', 'end', text=self.tr(title), open=False) for kind,title in
                 (('category','Catégories'),('property','Tags'),('continent','Continents'),('region','Régions'))}
        local_iid = category_tree.insert('', 'end', text=self.tr('Ajouts locaux'))
        category_map, property_map, continent_map, region_map = {}, {}, {}, {}
        geographic = []
        for region in self.model.geographic_regions():
            outline = decode_region_outline(region)
            if outline: geographic.append((region['name'],region['group_id'],outline))
        def fill_categories(*_):
            for root in roots.values(): category_tree.delete(*category_tree.get_children(root))
            for mapping in (category_map, property_map, continent_map, region_map): mapping.clear()
            needle = folded(filter_text.get())
            for ident, category in sorted(self.model.categories.items(), key=lambda item: item[1]['name']):
                if needle in folded(category['name']):
                    iid = category_tree.insert(roots['category'], 'end', text=category['name']); category_map[iid] = ident
            for flag in sorted(self.model.db['flags'].values(), key=lambda item: item['name']):
                if flag['name'] and needle in folded(flag['name']):
                    iid = category_tree.insert(roots['property'], 'end', text=flag['name']); property_map[iid] = flag['name']
            for continent in sorted(self.model.db['continents'], key=lambda item: item['name']):
                if needle in folded(continent['name']):
                    iid = category_tree.insert(roots['continent'], 'end', text=continent['name']); continent_map[iid] = continent['id']
            for name, group, outline in geographic:
                if needle in folded(name):
                    iid = category_tree.insert(roots['region'], 'end', text=name); region_map[iid] = (group,outline)
        fill_categories(); filter_text.trace_add('write', fill_categories)
        tk.Label(right, text='Paramètres de recherche actuels').pack(anchor='w')
        summary = tk.Text(right, height=5, width=30); summary.pack(fill='x'); summary.insert('1.0', translate('Sélectionnez les critères, puis Chercher.', self.ui_language.get()))
        tk.Label(right, text='Historique des recherches').pack(anchor='w', pady=(5, 0))
        history = tk.Listbox(right); history.pack(fill='both', expand=True)
        for value in getattr(self, 'search_history', []): history.insert('end', value)
        buttons = tk.Frame(right); buttons.pack(fill='x')
        def run():
            if not names.get() and not comments.get():
                messagebox.showerror(self.tr('Recherche'), self.tr('Sélectionner Nom ou Commentaires.'), parent=dialog); return
            try:
                minimum, maximum = (int(low.get()), int(high.get())) if use_level.get() else (None, None)
            except ValueError:
                messagebox.showerror(self.tr('Recherche'), self.tr('Les niveaux doivent être des nombres.'), parent=dialog); return
            chosen = category_tree.selection()
            categories = {category_map[x] for x in chosen if x in category_map} or None
            properties = [property_map[x] for x in chosen if x in property_map] or None
            continents = {continent_map[x] for x in chosen if x in continent_map} or None
            regions = [region_map[x] for x in chosen if x in region_map] or None
            self.search_options = dict(name=names.get(), comment=comments.get(), categories=categories, minimum=minimum,
                                       maximum=maximum, properties=properties, continents=continents, regions=regions,
                                       local_only=local_iid in chosen)
            self.search_history = [self.query.get()] + getattr(self, 'search_history', [])[:19]
            self.search(); dialog.destroy()
        tk.Button(buttons, text='Annuler', command=dialog.destroy, width=9).pack(side='right', padx=3)
        tk.Button(buttons, text='Chercher', command=run, width=9).pack(side='right')
        tk.Button(left, text='Initialiser les paramètres', command=lambda: self.query.set('')).pack(side='left')
        tk.Button(left, text='Options', command=self.not_implemented).pack(side='right')
        self.apply_ui_language()
        if self.dark_theme.get():
            self.apply_theme()
        dialog.bind('<Return>', lambda event: run())

    def close(self):
        if self.model.index:
            self.model.index.close()
        self.store.close()
        self.root.destroy()

    def change_mode(self):
        self.show_pane('regions')
        self.root.title(self.tr('WoWCarto Forever — Modern : base historique 1.20' if self.mode.get() == 'Modern'
                                else 'WoWCarto Forever — client intégré en validation'))
        for iid in getattr(self, 'region_ids', []):
            if self.regions.exists(iid): self.regions.delete(iid)
        self.region_ids = []
        self.region_filter.set('')
        self.continent_filter.set('All' if self.ui_language.get() == 'en' else 'Tous')
        self.region_continents = {}
        self.map_tiles_by_group = {}
        self.forever_by_key = {}
        self.forever_by_group = {}
        self.minimap_assets = {}
        self.legacy_plans = []
        self.results_tree.delete(*self.results_tree.get_children())
        self.search_matches, self.results_offset = [], 0
        self.results_page.set('')
        self.previous_results.configure(state='disabled'); self.next_results.configure(state='disabled')
        self.detail.delete('1.0', 'end')
        self.query.set('')
        self.groups, self.results, self.selection = {}, {}, None
        self.shown_group = None
        self.shown_region = None
        self.region_centers = {}
        self.region_outlines = {}
        self.region_native_ids = {}
        self.map.region_markers = []
        self.map.region_outline = []
        selected = next(s for s in self.model.sets if s['mode'] == self.mode.get())
        if self.mode.get() == 'Référence 1.20' or (self.mode.get() == 'Legacy' and selected.get('databases')):
            self.model.open(selected['id'], self.database.get())
            self.load_category_icons()
            tiles_by_group = {}
            for tile in self.model.tiles.values():
                tiles_by_group.setdefault(tile['group_id'], []).append(tile)
            self.map_tiles_by_group = tiles_by_group
            continents = {c['id']: c['name'] for c in self.model.db['continents']}
            for row in self.model.geographic_regions():
                tiles = tiles_by_group.get(row['group_id'])
                if tiles:
                    iid = self.regions.insert('', 'end', text=row['name'], image=self.map.region_icon or '')
                    self.region_native_ids[iid] = row['id']
                    self.groups[iid] = (row['group_id'], tiles)
                    self.region_centers[iid] = (row['wx'], row['wy'])
                    outline = decode_region_outline(row)
                    if outline:
                        self.region_outlines[iid] = outline
                    group = self.model.groups[row['group_id']]
                    self.region_continents[iid] = (group['name'] if folded(group['name']) == 'les trefonds'
                                                   else continents.get(group['continent_id'], group['name']))
            self.label.set(self.tr('Base 1.20 : annotations natives. Positions validées sur sept tuiles ; autres positions expérimentales.'))
            if self.mode.get() == 'Legacy':
                self.label.set(self.tr('Legacy candidat : base 1.08b, fonds continentaux 1.20 compatibles ; archive non authentifiée, une tuile absente.'))
                manifest = local_file(self.data, selected['legacy_pack'])
                for row in load_pack(manifest)['maps']:
                    self.legacy_plans.append((row['title'], dict(id=row['id'], filename=row['title'], x=0, y=0,
                        width=256, height=256, root=manifest.parent, standard=row['standard'], high=None)))
        elif self.mode.get() == 'Modern':
            self.model.open('reference120', self.database.get())
            self.load_category_icons()
            manifest = local_file(self.data, selected['terrain_pack'])
            pack = load_pack(manifest)
            preview_path = manifest.parent / 'previews.json'
            previews = json.loads(preview_path.read_text(encoding='utf-8')) if preview_path.is_file() else {}
            overview_path = self.data / 'minimaps' / 'manifest.json'
            if overview_path.is_file():
                try:
                    overview = json.loads(overview_path.read_text(encoding='utf-8'))
                    if overview.get('format') == 'wcf-minimaps-v1' and isinstance(overview.get('groups'), dict):
                        self.minimap_assets = overview['groups']
                except (OSError, ValueError):
                    pass
            modern_groups = {}
            for name in ('Azeroth', 'Kalimdor'):
                tiles = []
                for row in pack['maps']:
                    if row['group'] != name:
                        continue
                    m = re.fullmatch(r'map(\d+)_(\d+)', row['title'], re.I)
                    if m:
                        tiles.append(dict(id=row['id'], filename=row['title'], x=int(m[1])*256,
                                          y=int(m[2])*256, width=256, height=256,
                                          root=manifest.parent, standard=row['standard'], high=None, preview=previews.get(row['id'])))
                modern_groups[name] = tiles
            self.map_tiles_by_group = modern_groups
            for converted in project_historical_records(self.model.annotations):
                self.forever_by_key[converted['key']] = converted
                self.forever_by_group.setdefault(converted['group_id'], []).append(converted)
            for row in self.model.geographic_regions():
                registration = FOREVER_REGISTRATION.get(row['group_id'])
                outline = decode_region_outline(row)
                if not registration or not outline:
                    continue
                name = registration[0]
                tiles = modern_groups.get(name, [])
                if not tiles:
                    continue
                iid = self.regions.insert('', 'end', text=row['name'], image=self.map.region_icon or '')
                self.region_native_ids[iid] = row['id']
                self.groups[iid] = (name, tiles)
                self.region_continents[iid] = name
                self.region_centers[iid] = project_forever(row['group_id'], (row['wx'], row['wy']))
                self.region_outlines[iid] = [project_forever(row['group_id'], p) for p in outline]
            self.label.set(self.tr('Forever : annotations historiques 1.20 recalées sur le terrain. Emplacements et contenu non vérifiés dans Forever ; ils peuvent être périmés.'))
        else:
            self.map.icons = {}
            self.map.region_icon = None
            manifest = local_file(self.data, selected['legacy_pack'])
            pack = load_pack(manifest)
            for row in pack['maps']:
                tile = dict(id=row['id'], filename=row['title'], x=0, y=0, width=256, height=256,
                            root=manifest.parent, standard=row['standard'], high=None)
                self.legacy_plans.append((row['title'], tile))
            self.label.set(self.tr('1.08b : 26 plans issus d’une archive tierce non authentifiée. Continents et base non intégrés.'))
            self.group = None
            self.map.set_scene([], [])
            self.status.set(self.tr('Aucune zone géographique Legacy intégrée. Les plans restent accessibles par le menu Plans Legacy.'))
        coverage = self.model.translation_coverage.get(self.database.get())
        if coverage and self.mode.get() != 'Legacy':
            self.label.set(self.label.get() + f" Entrées avec traduction : {coverage['translated_annotations']}/{coverage['total_annotations']} ; textes manquants en EN.")
        entries = self.regions.get_children()
        self.region_ids = list(entries)
        self.continent_box['values'] = (('All' if self.ui_language.get() == 'en' else 'Tous'), *sorted(set(self.region_continents.values())))
        if entries:
            self.regions.selection_set(entries[0])
            self.select_group()

    def select_group(self, event=None):
        selected = self.regions.selection()
        if not selected or selected[0] not in self.groups:
            return
        iid = selected[0]
        self.group, self.tiles = self.groups[iid]
        if self.shown_group != self.group:
            self.shown_group = self.group
            if self.mode.get() in ('Référence 1.20', 'Legacy'):
                records = [r for r in self.model.by_group.get(self.group, []) if r['category'] != 18]
            elif self.mode.get() == 'Modern':
                records = self.forever_by_group.get(self.group, [])
            else:
                records = []
            self.map.set_scene(self.tiles, records)
            source = ((' · historical 1.20 data not verified in Forever' if self.ui_language.get() == 'en'
                       else ' · données 1.20 historiques non vérifiées dans Forever') if self.mode.get() == 'Modern' else '')
            if self.mode.get() == 'Legacy':
                source += ' · ' + self.tr('Legacy candidat : base 1.08b, fonds continentaux 1.20 compatibles ; archive non authentifiée, une tuile absente.')
            coverage = self.model.translation_coverage.get(self.database.get())
            if coverage:
                source += f" · traduction : {coverage['translated_annotations']}/{coverage['total_annotations']} entrées ; reste en EN"
            if self.ui_language.get() == 'en':
                self.status.set(f'{self.tr(self.mode.get())}: {len(self.tiles)} tiles, {len(records)} annotations available{source}.')
            else:
                self.status.set(f'{self.mode.get()} : {len(self.tiles)} tuiles, {len(records)} annotations disponibles{source}.')
        self.map.region_markers = [dict(iid=key, wx=point[0], wy=point[1])
                                   for key, point in self.region_centers.items()
                                   if self.groups[key][0] == self.group]
        if self.shown_region != iid:
            self.shown_region = iid
            self.map.region_outline = self.region_outlines.get(iid, [])
            if self.map.region_outline:
                self.map.fit_bounds(polygon_bounds(self.map.region_outline))
            elif iid in self.region_centers:
                wx, wy = self.region_centers[iid]
                self.map.center(wx, wy, .8)

    def select_region_marker(self, iid):
        if iid not in self.groups:
            return
        if iid not in self.regions.get_children():
            self.region_filter.set('')
            self.continent_filter.set('All' if self.ui_language.get() == 'en' else 'Tous')
            self.filter_regions()
        self.regions.selection_set(iid)
        self.regions.see(iid)
        self.select_group()

    def view_changed(self, canvas):
        self.mini.delete('all')
        x0, y0, x1, y1 = canvas.bounds()
        scale = min(100 / max(1, x1-x0), 100 / max(1, y1-y0))
        self.mini_transform = (x0, y0, scale)
        key = (self.mode.get(), getattr(self, 'group', None))
        if key != getattr(self, 'mini_key', None):
            self.mini_key = key
            if len(canvas.tiles) <= 100:
                preview, _ = canvas.miniature((100, 100))
                self.mini_photo = ImageTk.PhotoImage(preview)
            else:
                self.mini_photo = None
                asset = self.minimap_assets.get(getattr(self, 'group', None))
                if self.mode.get() == 'Modern' and isinstance(asset, dict):
                    try:
                        if asset.get('bounds') == list(canvas.bounds()) and asset.get('tiles') == len(canvas.tiles):
                            preview = checked_image(self.data / 'minimaps', asset)
                            self.mini_photo = ImageTk.PhotoImage(preview)
                    except (OSError, ValueError, KeyError, TypeError):
                        pass
        if self.mini_photo:
            self.mini.create_image(0, 0, image=self.mini_photo, anchor='nw')
        else:
            for tile in canvas.tiles:
                x, y = (tile['x']-x0)*scale, (tile['y']-y0)*scale
                self.mini.create_rectangle(x, y, x+256*scale, y+256*scale, fill='#b8c4a8', outline='')
        w, h = canvas.winfo_width()/canvas.scale, canvas.winfo_height()/canvas.scale
        self.mini.create_rectangle((canvas.cx-w/2-x0)*scale, (canvas.cy-h/2-y0)*scale,
                                   (canvas.cx+w/2-x0)*scale, (canvas.cy+h/2-y0)*scale, outline='yellow')

    def mini_click(self, event):
        x0, y0, scale = self.mini_transform
        self.map.center(x0 + event.x/scale, y0 + event.y/scale)

    def search(self):
        self.results_tree.delete(*self.results_tree.get_children())
        self.results = {}
        self.search_matches, self.results_offset = [], 0
        self.results_page.set('')
        self.previous_results.configure(state='disabled'); self.next_results.configure(state='disabled')
        if self.mode.get() not in ('Référence 1.20', 'Modern') and not (self.mode.get() == 'Legacy' and self.model.map_set.get('id') == 'legacy108'):
            self.status.set(self.tr('Aucune base d’annotations calibrée pour ce mode.'))
            return
        query = self.query.get().strip()
        options = getattr(self, 'search_options', {'comment': True})
        if not query and not any(options.get(key) is not None for key in ('categories', 'minimum', 'maximum', 'properties', 'continents', 'regions')) and not options.get('local_only'):
            self.status.set(self.tr('Saisir un nom ou un texte à rechercher.'))
            return
        matches = self.model.search(query, **options)
        if self.mode.get() == 'Modern':
            matches = [self.forever_by_key[row['key']] for row in matches if row['key'] in self.forever_by_key]
        self.search_matches, self.results_offset = matches, 0
        self.render_results()
        self.show_pane('results')

    def render_results(self):
        self.results_tree.delete(*self.results_tree.get_children())
        self.results = {}
        total = len(self.search_matches)
        end = min(self.results_offset + 500, total)
        for row in self.search_matches[self.results_offset:end]:
            iid = self.results_tree.insert('', 'end', text=row['name'] + ' — ' + row['region'])
            self.results[iid] = row
        first = self.results_offset + 1 if total else 0
        self.results_page.set(f'{first}–{end} / {total}')
        self.previous_results.configure(state='normal' if self.results_offset else 'disabled')
        self.next_results.configure(state='normal' if end < total else 'disabled')
        self.status.set(self.tr('{count} résultat(s).').format(count=total))

    def move_results(self, direction):
        target = self.results_offset + direction * 500
        if 0 <= target < len(self.search_matches):
            self.results_offset = target
            self.render_results()

    def select_result(self, event=None):
        chosen = self.results_tree.selection()
        if not chosen or chosen[0] not in self.results:
            return
        row = self.results[chosen[0]]
        tiles = self.map_tiles_by_group.get(row['group_id'])
        if tiles:
            self.group, self.tiles = row['group_id'], tiles
            if self.shown_group != self.group:
                self.shown_group = self.group
                records = (self.forever_by_group.get(self.group, []) if self.mode.get() == 'Modern'
                           else self.model.by_group.get(self.group, []))
                self.map.set_scene(tiles, records)
            self.shown_region = None
            self.map.region_outline = []
            self.map.region_markers = [dict(iid=key, wx=point[0], wy=point[1])
                                       for key, point in self.region_centers.items()
                                       if self.groups[key][0] == self.group]
            self.regions.selection_remove(*self.regions.selection())
            self.map.selected = row['key']
            self.map.center(row['wx'], row['wy'], .2 if self.mode.get() == 'Modern' else .8)
            self.select_record(row)

    def show_legacy_plans(self):
        if self.mode.get() != 'Legacy':
            self.status.set(self.tr('Sélectionner le mode Legacy pour consulter les plans.'))
            return
        dialog = tk.Toplevel(self.root); dialog.title('Plans Legacy'); dialog.geometry('340x420')
        listing = tk.Listbox(dialog); listing.pack(fill='both', expand=True)
        for name, _ in self.legacy_plans:
            listing.insert('end', name)
        def select(event=None):
            indexes = listing.curselection()
            if indexes:
                name, tile = self.legacy_plans[indexes[0]]
                self.group, self.tiles, self.shown_group = tile['id'], [tile], tile['id']
                self.map.region_markers = []
                self.map.region_outline = []
                self.map.set_scene([tile], [])
                self.status.set(f'Plan Legacy : {name}. Aucune zone géographique intégrée.')
                dialog.destroy()
        listing.bind('<Double-Button-1>', select)
        tk.Button(dialog, text='Afficher', command=select).pack()
        self.apply_ui_language()
        if self.dark_theme.get():
            self.apply_theme()

    def select_record(self, row):
        self.selection = row
        self.detail.delete('1.0', 'end')
        category = self.model.categories.get(row['category'], {}).get('name', str(row['category']))
        if self.ui_language.get() == 'en':
            provenance = '\nSource: historical 1.20 database; presence and position are not verified in Forever.\n' if row.get('historical_unverified') else ''
            level_label = 'Level'
        else:
            provenance = '\nSource : base historique 1.20 ; présence et position non vérifiées dans Forever.\n' if row.get('historical_unverified') else ''
            level_label = 'Niveau'
        self.detail.insert('1.0', f"{row['name']}\n{category} · {row['region']}\n{level_label} {row.get('min_level', 0)}–{row.get('max_level', 0)}\n{provenance}\n{row.get('comment', '')}")

    def context(self, event, record, tile, wx, wy):
        menu = tk.Menu(self.root, tearoff=False)
        if self.mode.get() == 'Référence 1.20' and tile:
            menu.add_command(label='Ajouter une annotation…', command=lambda: self.add_note(tile, wx, wy))
        if record and self.mode.get() == 'Référence 1.20':
            menu.add_command(label=self.tr('Modifier l’annotation locale…'), command=lambda: self.edit_note(record))
            if record.get('note_id'):
                menu.add_command(label=self.tr('Masquer l’annotation locale…'), command=lambda: self.hide_local_note(record))
                if (self.database.get() == 'Francais' and
                        str(record.get('source_ref', '')).startswith(self.model.personal_prefix)):
                    menu.add_command(label=self.tr('Partager cette annotation…'),
                                     command=lambda: self.share_personal_note(record))
            if record.get('source') == 'community' and self.database.get() == 'Francais':
                menu.add_command(label=self.tr('Corriger pour la communauté…'),
                                 command=lambda: self.correct_community(record))
            if record.get('source') == 'native' and self.database.get() in SOURCES:
                menu.add_command(label=self.tr('Corriger pour la communauté…'),
                                 command=lambda: self.correct_reference(record))
        elif record and self.mode.get() == 'Modern' and record.get('source') == 'native' and self.database.get() in SOURCES:
            menu.add_command(label=self.tr('Corriger pour la communauté…'),
                             command=lambda: self.correct_reference(record))
        if menu.index('end') is None:
            menu.add_command(label='Annotations non calibrées pour cette carte', state='disabled')
        self.apply_ui_language()
        if self.dark_theme.get():
            self.apply_theme()
        menu.tk_popup(event.x_root, event.y_root)

    def add_note(self, tile, wx, wy):
        if not self.can_edit():
            return
        initial = dict(name='', map_id=tile['id'], x=max(0, min(65535, int((wx-tile['x'])*256))),
                       y=max(0, min(65535, int((wy-tile['y'])*256))), category=37)
        data = AnnotationDialog(self.root, initial, self.model.categories, self.tr,
                                self.apply_theme if self.dark_theme.get() else None).result
        if data is None: return
        data['source_ref'] = self.model.personal_prefix + str(uuid.uuid4())
        try:
            self.store.add_note(data)
            self.refresh_local_annotations()
            self.status.set(self.tr('Annotation locale enregistrée.'))
        except ValueError as error:
            messagebox.showerror('Annotation', str(error), parent=self.root)

    def edit_note(self, row):
        if not self.can_edit():
            return
        data = AnnotationDialog(self.root, row, self.model.categories, self.tr,
                                self.apply_theme if self.dark_theme.get() else None).result
        if data is None: return
        try:
            if row.get('note_id'):
                self.store.update_note(row['note_id'], data, expected_revision=row.get('revision'))
            else:
                self.store.set_override(row['key'], data)
            self.refresh_local_annotations()
            self.status.set(self.tr('Annotation locale enregistrée.'))
        except (ValueError, KeyError) as error:
            messagebox.showerror(self.tr('Annotation locale'), str(error), parent=self.root)

    def refresh_local_annotations(self):
        self.model.rebuild()
        records = [r for r in self.model.by_group.get(getattr(self, 'group', None), []) if r['category'] != 18]
        if self.tiles:
            self.map.set_scene(self.tiles, records, fit=False)

    def hide_local_note(self, row):
        if not self.can_edit() or not row.get('note_id'): return
        if not messagebox.askyesno(self.tr('Annotation locale'),
                                   self.tr('Masquer cette annotation locale ? Elle pourra être restaurée.'), parent=self.root): return
        try:
            self.store.delete_note(row['note_id'], expected_revision=row.get('revision'))
            self.refresh_local_annotations()
            self.status.set(self.tr('Annotation locale masquée.'))
        except (ValueError, KeyError) as error:
            messagebox.showerror(self.tr('Annotation locale'), str(error), parent=self.root)

    def restore_local_note(self):
        if not self.can_edit(): return
        prefixes = (self.model.native_prefix, self.model.personal_prefix)
        community_refs = ({row['source_ref'] for row in self.model.community if row.get('source_ref')}
                          if self.database.get() == 'Francais' else set())
        notes = [row for row in self.store.notes(include_deleted=True)
                 if row['deleted'] and isinstance(row.get('source_ref'), str)
                 and (row['source_ref'].startswith(prefixes) or row['source_ref'] in community_refs)]
        if not notes:
            messagebox.showinfo(self.tr('Annotations masquées'), self.tr('Aucune annotation masquée dans cette base.'), parent=self.root)
            return
        dialog = tk.Toplevel(self.root); dialog.title(self.tr('Annotations masquées')); dialog.transient(self.root)
        dialog.geometry('420x320')
        listing = tk.Listbox(dialog, exportselection=False)
        listing.pack(fill='both', expand=True, padx=8, pady=8)
        for note in notes: listing.insert('end', note['name'])
        def restore():
            selection = listing.curselection()
            if not selection: return
            note = notes[selection[0]]
            try:
                self.store.restore_note(note['id'], expected_revision=note['revision'])
                self.refresh_local_annotations()
                self.status.set(self.tr('Annotation locale restaurée.'))
                dialog.destroy()
            except (ValueError, KeyError) as error:
                messagebox.showerror(self.tr('Annotations masquées'), str(error), parent=dialog)
        tk.Button(dialog, text=self.tr('Restaurer'), command=restore).pack(pady=(0,8))
        if self.dark_theme.get(): self.apply_theme()

    def export_notes(self):
        path = filedialog.asksaveasfilename(defaultextension='.json', title=self.tr('Exporter mes annotations'))
        if path:
            try:
                self.store.export_notes(path)
                self.status.set(self.tr('Annotations exportées.'))
            except (OSError, ValueError) as error:
                messagebox.showerror('Export', str(error), parent=self.root)

    def correct_community(self, row):
        if not self.can_edit() or self.database.get() != 'Francais':
            return
        edited = AnnotationDialog(self.root, row, self.model.categories, self.tr,
                                  self.apply_theme if self.dark_theme.get() else None,
                                  editable_tags=False, title='Correction communautaire').result
        if edited is None:
            return
        changes = {key: edited[key] for key in ('name', 'comment', 'category', 'min_level', 'max_level')
                   if edited[key] != row.get(key)}
        if not changes: return
        if not messagebox.askyesno(self.tr('Correction communautaire'),
                                   self.tr('Ces modifications seront publiées pour tous. Continuer ?'), parent=self.root):
            return
        self.status.set(self.tr('Envoi de la correction…'))
        def work():
            try:
                submit_correction_fields(row, changes)
            except HTTPError as error:
                message = ('Une autre personne a modifié cette annotation. Synchronisez avant de réessayer.'
                           if error.code == 409 else 'Correction indisponible : ' + str(error))
                self.root.after(0, lambda: self.status.set(self.tr(message)))
                return
            except Exception as error:
                self.root.after(0, lambda message=str(error): self.status.set(self.tr('Correction indisponible : ') + message))
                return
            try:
                result = self.community.sync()
                if result['status'] != 'online':
                    raise ValueError('Synchronisation indisponible.')
                self.root.after(0, lambda: self.sync_complete(result))
            except Exception:
                self.root.after(0, lambda: self.status.set(self.tr('Correction envoyée. Synchronisez pour actualiser l’affichage.')))
        threading.Thread(target=work, daemon=True).start()

    def share_personal_note(self, row):
        if not self.can_edit() or self.database.get() != 'Francais': return
        try:
            data = personal_addition(row)
        except ValueError as error:
            messagebox.showerror(self.tr('Partager'), self.tr(str(error)), parent=self.root)
            return
        question = self.tr('Cette annotation sera publiée dans la base française. Vérifiez son contenu et ses coordonnées. La copie personnelle restera indépendante. Continuer ?')
        if not messagebox.askyesno(self.tr('Partager'), question, parent=self.root): return
        self.status.set(self.tr('Envoi communautaire en cours…'))
        def work():
            try:
                result = submit_additions(data)
            except Exception as error:
                self.root.after(0, lambda message=str(error): self.status.set(self.tr('Partage indisponible : ') + message))
                return
            try:
                synced = self.community.sync()
                if synced['status'] != 'online': raise ValueError('Synchronisation indisponible.')
                def complete():
                    self.sync_complete(synced)
                    self.status.set(self.tr('Annotation partagée.') if result['inserted']
                                    else self.tr('Cette annotation a déjà été partagée.'))
                self.root.after(0, complete)
            except Exception:
                self.root.after(0, lambda: self.status.set(self.tr('Partage envoyé. Synchronisez pour actualiser l’affichage.')))
        threading.Thread(target=work, daemon=True).start()

    def correct_reference(self, row):
        if not self.can_edit(allow_modern=True): return
        locale = self.database.get()
        sha = self.model.db.get('source_sha256')
        if SOURCES.get(locale) != sha:
            self.status.set(self.tr('Base de référence incompatible avec le service.')); return
        name = simpledialog.askstring(self.tr('Correction communautaire'), self.tr('Nom :'),
                                      initialvalue=row['name'], parent=self.root)
        if not name or name == row['name']: return
        if not messagebox.askyesno(self.tr('Correction communautaire'),
                                   self.tr('Ce nom modifiera la référence historique partagée et sera affiché en référence 1.20 et en Modern. Continuer ?'), parent=self.root): return
        self.status.set(self.tr('Envoi de la correction…'))
        def work():
            try:
                submit_reference(locale, sha, row['id'], row.get('reference_revision', 0), name)
            except HTTPError as error:
                message = ('Une autre personne a modifié cette annotation. Synchronisez avant de réessayer.'
                           if error.code == 409 else 'Correction indisponible : ' + str(error))
                self.root.after(0, lambda: self.status.set(self.tr(message))); return
            except Exception as error:
                self.root.after(0, lambda message=str(error): self.status.set(self.tr('Correction indisponible : ') + message)); return
            try:
                result = self.reference_community.sync(locale, sha)
                if result['status'] != 'online': raise ValueError('Synchronisation indisponible.')
                self.root.after(0, lambda: self.reference_sync_complete(locale, result))
            except Exception:
                self.root.after(0, lambda: self.status.set(self.tr('Correction envoyée. Synchronisez pour actualiser l’affichage.')))
        threading.Thread(target=work, daemon=True).start()

    def import_notes(self):
        path = filedialog.askopenfilename(title=self.tr('Importer mes annotations'), filetypes=[('JSON', '*.json')])
        if path:
            try:
                result = self.store.import_notes(path)
                self.model.rebuild()
                self.shown_group = None
                self.select_group()
                self.status.set(str(result))
            except (OSError, ValueError) as error:
                messagebox.showerror('Import', str(error), parent=self.root)

    def share_additions(self):
        if self.mode.get() != 'Référence 1.20' or self.database.get() != 'Francais':
            self.status.set(self.tr('Le partage est limité aux ajouts de la base française 1.20.'))
            return
        path = filedialog.askopenfilename(title=self.tr('Partager un fichier d’ajouts'),
                                          filetypes=[('WoWCartographe', '*.txt')])
        if not path:
            return
        try:
            with Path(path).open('rb') as stream:
                data = stream.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise ValueError('Fichier supérieur à 1 Mio.')
            parse_additions(data)
        except (OSError, ValueError) as error:
            messagebox.showerror(self.tr('Partager'), self.tr(str(error)), parent=self.root)
            return
        question = self.tr('Le fichier et ses annotations seront transmis à la base communautaire française. Vérifiez la langue et l’absence d’informations personnelles. Continuer ?')
        if not messagebox.askyesno(self.tr('Partager'), question, parent=self.root):
            return
        self.status.set(self.tr('Envoi communautaire en cours…'))
        def work():
            try:
                result = submit_additions(data)
                count = result['records']
                def complete():
                    if result['inserted']:
                        message = f'{count} annotations shared.' if self.ui_language.get() == 'en' else f'{count} annotations partagées.'
                    else:
                        message = self.tr('Ce fichier a déjà été partagé.')
                    self.status.set(message)
                self.root.after(0, complete)
            except Exception as error:
                self.root.after(0, lambda message=str(error): self.status.set(self.tr('Partage indisponible : ') + self.tr(message)))
        threading.Thread(target=work, daemon=True).start()

    def sync(self):
        if self.mode.get() not in ('Référence 1.20', 'Modern') or self.database.get() not in SOURCES:
            self.status.set(self.tr('La réception communautaire est limitée aux bases FR et EN 1.20.'))
            return
        messagebox.showinfo(self.tr('À propos des données'),
                            self.tr('Les données communautaires peuvent contenir des erreurs ou des informations anciennes. Elles sont destinées à être améliorées par la communauté.'),
                            parent=self.root)
        self.status.set(self.tr('Réception communautaire en cours…'))
        locale = self.database.get()
        sha = self.model.db.get('source_sha256')
        def work():
            try:
                result = self.reference_community.sync(locale, sha)
                self.root.after(0, lambda: self.reference_sync_complete(locale, result))
            except Exception as error:
                self.root.after(0, lambda message=str(error): self.status.set(self.tr('Synchronisation indisponible : ') + message))
            if locale == 'Francais':
                try:
                    additions = self.community.sync()
                    self.root.after(0, lambda: self.sync_complete(additions))
                except Exception as error:
                    self.root.after(0, lambda message=str(error): self.status.set(self.tr('Synchronisation indisponible : ') + message))
        threading.Thread(target=work, daemon=True).start()

    def reference_sync_complete(self, locale, result):
        self.model.reference_corrections[locale] = {r['id']:r for r in result['items']}
        if self.mode.get() in ('Référence 1.20', 'Modern') and self.database.get() == locale:
            self.model.rebuild()
            regions = {row['id']:row['name'] for row in self.model.geographic_regions()}
            for iid, ident in self.region_native_ids.items():
                if ident in regions and self.regions.exists(iid):
                    self.regions.item(iid, text=regions[ident])
            if self.mode.get() == 'Modern':
                self.forever_by_key, self.forever_by_group = {}, {}
                for converted in project_historical_records(self.model.annotations):
                    self.forever_by_key[converted['key']] = converted
                    self.forever_by_group.setdefault(converted['group_id'], []).append(converted)
                records = self.forever_by_group.get(getattr(self, 'group', None), [])
            else:
                records = [r for r in self.model.by_group.get(getattr(self, 'group', None), []) if r['category'] != 18]
            if self.tiles:
                self.map.set_scene(self.tiles, records, fit=False)
        lang = 'FR' if locale == 'Francais' else 'EN'
        state = self.tr('à jour' if result['status'] == 'online' else 'cache local')
        self.status.set(self.tr('Corrections {language} : {count} · {state} {error}').format(language=lang,count=len(result['items']),state=state,error=result.get('error') or ''))

    def sync_complete(self, result):
        self.model.community = result['annotations']
        self.model.rebuild()
        if self.mode.get() == 'Modern':
            self.forever_by_key, self.forever_by_group = {}, {}
            for converted in project_historical_records(self.model.annotations):
                self.forever_by_key[converted['key']] = converted
                self.forever_by_group.setdefault(converted['group_id'], []).append(converted)
            records = self.forever_by_group.get(getattr(self, 'group', None), [])
        else:
            records = [r for r in self.model.by_group.get(getattr(self, 'group', None), []) if r['category'] != 18]
        if self.tiles and self.mode.get() in ('Référence 1.20', 'Modern'):
            self.map.set_scene(self.tiles, records, fit=False)
        state = self.tr('à jour' if result['status'] == 'online' else 'cache local')
        self.status.set(self.tr('Communauté : {state}, {count} annotations. {error}').format(state=state,count=len(result['annotations']),error=result.get('error') or ''))


def main():
    parser = argparse.ArgumentParser()
    home = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    parser.add_argument('--data', type=Path, default=home / 'data')
    parser.add_argument('--state', type=Path, default=Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'WoWCartoForeverClone' / 'annotations.sqlite3')
    args = parser.parse_args()
    app = Application(args.data, args.state)
    messagebox.showinfo(app.tr('À propos des données'),
                        app.tr('Les bases FR et EN proviennent de WoWCartographe 1.20. Elles peuvent contenir des erreurs ou des informations anciennes. En mode Modern, leur présence et leur position dans WoW Forever ne sont pas toutes vérifiées. La communauté pourra les corriger au fur et à mesure.'),
                        parent=app.root)
    app.root.mainloop()


if __name__ == '__main__':
    main()
