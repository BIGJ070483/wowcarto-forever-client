"""Continuous map canvas for the reconstructed client; no filename grid inference."""
from __future__ import annotations

from collections import OrderedDict
from itertools import chain
import math
from pathlib import Path
import tkinter as tk

from PIL import Image, ImageEnhance, ImageTk

from tools.map_packs import checked_image


class MapCanvas(tk.Canvas):
    def __init__(self, owner, *, on_select, on_context, on_view, status, translate=lambda text: text, **kwargs):
        super().__init__(owner, background='#63859c', highlightthickness=1,
                         highlightbackground='#808080', takefocus=True, **kwargs)
        self.on_select, self.on_context, self.on_view, self.status = on_select, on_context, on_view, status
        self.translate = translate
        self.tiles, self.records, self.photos, self.hits = [], [], [], []
        self.region_markers, self.region_hits, self.region_outline = [], [], []
        self.on_region_select = None
        self.region_icon = None
        self.cache, self.icons = OrderedDict(), {}
        self.cx = self.cy = 0.
        self.scale = 1.
        self.legend, self.dim, self.high = False, False, True
        self.selected, self.dragging, self.start = None, False, None
        self.pending = None
        self.bind('<Configure>', lambda e: self.schedule())
        self.bind('<ButtonPress-1>', self.press)
        self.bind('<B1-Motion>', self.drag)
        self.bind('<ButtonRelease-1>', self.release)
        self.bind('<Double-Button-1>', lambda e: self.zoom_to(1., e.x, e.y))
        self.bind('<Button-3>', self.context)
        self.bind('<MouseWheel>', lambda e: self.zoom_to(self.scale * (1.25 if e.delta > 0 else .8), e.x, e.y))
        self.bind('<Motion>', self.hover)
        for key, delta in [('Left', (-80, 0)), ('Right', (80, 0)), ('Up', (0, -80)), ('Down', (0, 80))]:
            self.bind('<' + key + '>', lambda e, d=delta: self.move(*d))
        self.bind('<Home>', lambda e: self.fit())

    def set_scene(self, tiles, records, *, fit=True):
        self.tiles, self.records, self.selected = list(tiles), list(records), None
        if fit:
            self.fit()
        else:
            self.schedule()

    def bounds(self):
        if not self.tiles:
            return (0, 0, 256, 256)
        return (min(t['x'] for t in self.tiles), min(t['y'] for t in self.tiles),
                max(t['x'] + t.get('width', 256) for t in self.tiles),
                max(t['y'] + t.get('height', 256) for t in self.tiles))

    def fit(self):
        self.fit_bounds(self.bounds(), padding=20)

    def fit_bounds(self, bounds, padding=50):
        x0, y0, x1, y1 = bounds
        self.cx, self.cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.scale = min(max(self.winfo_width() - padding, 100) / max(1, x1 - x0),
                         max(self.winfo_height() - padding, 100) / max(1, y1 - y0))
        self.scale = max(.025, min(8., self.scale))
        self.schedule()

    def zoom_to(self, scale, x=None, y=None):
        scale = max(.025, min(8., float(scale)))
        x = self.winfo_width() / 2 if x is None else x
        y = self.winfo_height() / 2 if y is None else y
        wx, wy = self.world(x, y)
        self.scale = scale
        self.cx = wx - (x - self.winfo_width() / 2) / scale
        self.cy = wy - (y - self.winfo_height() / 2) / scale
        self.schedule()

    def center(self, wx, wy, minimum_scale=None):
        self.cx, self.cy = wx, wy
        if minimum_scale is not None:
            self.scale = max(self.scale, minimum_scale)
        self.schedule()

    def move(self, dx, dy):
        self.cx += dx / self.scale
        self.cy += dy / self.scale
        self.schedule()

    def world(self, x, y):
        return (self.cx + (x - self.winfo_width() / 2) / self.scale,
                self.cy + (y - self.winfo_height() / 2) / self.scale)

    def screen(self, x, y):
        return ((x - self.cx) * self.scale + self.winfo_width() / 2,
                (y - self.cy) * self.scale + self.winfo_height() / 2)

    def tile_at(self, wx, wy):
        for tile in self.tiles:
            if tile['x'] <= wx < tile['x'] + tile.get('width', 256) and tile['y'] <= wy < tile['y'] + tile.get('height', 256):
                return tile
        return None

    def hit(self, x, y):
        found = [(abs(px - x) + abs(py - y), row) for px, py, row in self.hits if abs(px - x) < 11 and abs(py - y) < 11]
        return min(found, key=lambda item: item[0])[1] if found else None

    def press(self, event):
        self.focus_set()
        self.start = event.x, event.y, self.cx, self.cy
        self.dragging = False

    def drag(self, event):
        if self.start:
            x, y, cx, cy = self.start
            self.dragging = abs(event.x - x) + abs(event.y - y) > 3
            self.cx, self.cy = cx - (event.x - x) / self.scale, cy - (event.y - y) / self.scale
            self.schedule()

    def release(self, event):
        if not self.dragging:
            marker = next((r for x, y, r in reversed(self.region_hits)
                           if abs(x - event.x) < 8 and abs(y - event.y) < 8), None)
            if marker and self.on_region_select:
                self.on_region_select(marker['iid'])
                self.start = None
                return
            record = self.hit(event.x, event.y)
            if record:
                self.selected = record['key']
                self.on_select(record)
                self.schedule()
            elif event.state & 4:
                wx, wy = self.world(event.x, event.y)
                tile = self.tile_at(wx, wy)
                if tile:
                    self.center(tile['x'] + tile.get('width', 256) / 2, tile['y'] + tile.get('height', 256) / 2)
        self.start = None

    def context(self, event):
        record = self.hit(event.x, event.y)
        wx, wy = self.world(event.x, event.y)
        self.on_context(event, record, self.tile_at(wx, wy), wx, wy)

    def hover(self, event):
        record = self.hit(event.x, event.y)
        if record:
            levels = (self.translate(' · niveau {minimum}–{maximum}').format(minimum=record.get('min_level', 0),
                                                                            maximum=record.get('max_level', 0))
                      if record.get('max_level') else '')
            self.status(record['name'] + levels)

    def schedule(self):
        if self.pending is None:
            self.pending = self.after(12, self.draw)

    def asset_image(self, tile):
        asset = tile.get('preview') if self.scale < .35 and tile.get('preview') else (
            tile.get('high') if self.high and tile.get('high') else tile.get('standard'))
        if not asset:
            return None
        key = (str(tile['root']), asset['path'])
        if key not in self.cache:
            self.cache[key] = checked_image(tile['root'], asset)
            while len(self.cache) > 32:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]

    def draw(self):
        self.pending = None
        self.delete('all')
        self.photos, self.hits, self.region_hits = [], [], []
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        missing = []
        if not self.tiles:
            self.create_text(width / 2, height / 2, text=self.translate('Aucune carte disponible pour cette sélection.'), width=max(180, width - 80), justify='center')
        for tile in self.tiles:
            sx, sy = self.screen(tile['x'], tile['y'])
            tw, th = tile.get('width', 256) * self.scale, tile.get('height', 256) * self.scale
            if sx > width or sy > height or sx + tw < 0 or sy + th < 0:
                continue
            try:
                image = self.asset_image(tile)
                if image is None:
                    raise ValueError('Image absente')
                # Decode at native resolution, then only resize the visible crop.
                ix0 = max(0, math.floor(-sx / tw * image.width))
                iy0 = max(0, math.floor(-sy / th * image.height))
                ix1 = min(image.width, math.ceil((width - sx) / tw * image.width))
                iy1 = min(image.height, math.ceil((height - sy) / th * image.height))
                crop = image.crop((ix0, iy0, ix1, iy1))
                left, top = round(sx + ix0 / image.width * tw), round(sy + iy0 / image.height * th)
                right, bottom = round(sx + ix1 / image.width * tw), round(sy + iy1 / image.height * th)
                outw, outh = max(1, right - left), max(1, bottom - top)
                crop = crop.resize((outw, outh), Image.Resampling.BILINEAR)
                if self.dim:
                    crop = ImageEnhance.Brightness(crop).enhance(.55)
                photo = ImageTk.PhotoImage(crop)
                self.photos.append(photo)
                self.create_image(left, top, image=photo, anchor='nw')
            except (OSError, ValueError) as error:
                self.create_rectangle(sx, sy, sx + tw, sy + th, fill='#bababa', outline='#666666')
                missing.append(str(tile['id']))
        if len(self.region_outline) >= 3:
            coords = [value for point in self.region_outline for value in self.screen(*point)]
            self.create_line(*coords, *coords[:2], fill='#ffe35a', width=2)
        drawn = 0
        occupied = set()
        selected = None
        # User annotations must not disappear behind a dense historical layer.
        ordered = chain((row for row in self.records if row.get('source') == 'personal'),
                        (row for row in self.records if row.get('source') == 'community'),
                        (row for row in self.records if row.get('source') not in ('personal', 'community')))
        for row in ordered:
            px, py = self.screen(row['wx'], row['wy'])
            if not (-12 < px < width + 12 and -12 < py < height + 12):
                continue
            cell = (int(px // 12), int(py // 12))
            if (drawn >= 1800 or cell in occupied) and row['key'] != self.selected and row.get('source') != 'personal':
                continue
            occupied.add(cell)
            drawn += 1
            self.hits.append((px, py, row))
            if row['key'] == self.selected:
                selected = px, py, row
            icon = self.icons.get(row['category'])
            if icon:
                self.create_image(px, py, image=icon)
            else:
                color = '#5fd9ff' if row.get('source') == 'personal' else '#f5c850'
                self.create_oval(px - 3, py - 3, px + 3, py + 3, fill=color, outline='#2d2d2d')
            if self.legend:
                self.create_text(px + 10, py, text=row['name'], fill='#fff6bb', anchor='w', font=('Segoe UI', 8), tags=('legend',))
        if selected:
            px, py, row = selected
            self.create_oval(px - 10, py - 10, px + 10, py + 10, outline='#ffed00', width=2)
            self.create_text(px + 12, py - 10, text=row['name'], fill='#fff695', anchor='sw', font=('Segoe UI', 10, 'bold'))
        for marker in self.region_markers:
            px, py = self.screen(marker['wx'], marker['wy'])
            if 0 <= px <= width and 0 <= py <= height:
                self.region_hits.append((px, py, marker))
                if self.region_icon:
                    self.create_image(px, py, image=self.region_icon)
                else:
                    self.create_oval(px-4, py-4, px+4, py+4, fill='#ffe35a', outline='#333333')
        if missing:
            self.status(self.translate('{count} carte(s) absente(s) ou invalides dans cette vue ; aucune substitution.').format(count=len(missing)))
        self.on_view(self)

    def miniature(self, size=(150, 105)):
        x0, y0, x1, y1 = self.bounds()
        scale = min(size[0] / max(1, x1 - x0), size[1] / max(1, y1 - y0))
        image = Image.new('RGB', size, '#63859c')
        for tile in self.tiles:
            try:
                tile_image = self.asset_image(tile)
                if tile_image:
                    tile_image = tile_image.resize((max(1, round(tile.get('width', 256) * scale)), max(1, round(tile.get('height', 256) * scale))), Image.Resampling.BILINEAR)
                    image.paste(tile_image, (round((tile['x'] - x0) * scale), round((tile['y'] - y0) * scale)))
            except (OSError, ValueError):
                pass
        return image, (x0, y0, scale)
