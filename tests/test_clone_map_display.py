import tkinter as tk
import unittest

from tools.clone_map import MapCanvas


class MapDisplayTests(unittest.TestCase):
    def test_zoom_preserves_map_point_under_cursor_at_several_locations(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Fenêtre Tk indisponible : {error}')
        self.addCleanup(root.destroy)
        root.geometry('640x480')
        canvas = MapCanvas(root, on_select=lambda row: None, on_context=lambda *args: None,
                           on_view=lambda view: None, status=lambda message: None)
        canvas.pack(fill='both', expand=True); root.update()
        for center in [(-1024.,-1280.),(0.,0.),(12000.,-9400.)]:
            canvas.cx,canvas.cy=center
            for x,y in [(60,60),(320,240),(590,410)]:
                for scale in (.1,.5,1.,3.,7.5):
                    before=canvas.world(x,y)
                    canvas.zoom_to(scale,x,y)
                    after=canvas.world(x,y)
                    self.assertAlmostEqual(before[0],after[0],places=8)
                    self.assertAlmostEqual(before[1],after[1],places=8)
                    px,py=canvas.screen(*after)
                    self.assertAlmostEqual(px,x,places=8)
                    self.assertAlmostEqual(py,y,places=8)

    def test_personal_annotation_is_not_hidden_by_overlapping_native_point(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Fenêtre Tk indisponible : {error}')
        self.addCleanup(root.destroy)
        root.geometry('360x300')
        canvas = MapCanvas(root, on_select=lambda row: None, on_context=lambda *args: None,
                           on_view=lambda view: None, status=lambda message: None)
        canvas.pack(fill='both', expand=True); root.update()
        tile = dict(id='tile', x=0, y=0, width=256, height=256, root='.', standard=None, high=None)
        native = dict(key='native',wx=128,wy=128,category=1,name='Origine',source='native')
        personal = dict(key='personal',wx=128,wy=128,category=1,name='Personnel',source='personal')
        canvas.set_scene([tile],[native,personal]); root.update(); canvas.draw()
        x,y=canvas.screen(128,128)
        self.assertEqual(canvas.hit(x,y)['key'],'personal')

    def test_missing_tile_and_dense_annotations_do_not_show_diagnostic_numbers(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Fenêtre Tk indisponible : {error}')
        self.addCleanup(root.destroy)
        root.geometry('360x300')
        messages = []
        canvas = MapCanvas(root, on_select=lambda row: None, on_context=lambda *args: None,
                           on_view=lambda view: None, status=messages.append)
        canvas.pack(fill='both', expand=True)
        root.update()
        tile = dict(id='tile-123', filename='map12_34', x=0, y=0, width=256, height=256,
                    root='.', standard=None, high=None)
        records = [dict(key=str(i), wx=128, wy=128, category=1, name='Annotation') for i in range(5)]
        canvas.set_scene([tile], records)
        root.update()
        canvas.draw()
        texts = [canvas.itemcget(item, 'text') for item in canvas.find_all() if canvas.type(item) == 'text']
        self.assertEqual(texts, [])
        self.assertTrue(any('absente' in message for message in messages))


if __name__ == '__main__':
    unittest.main()
