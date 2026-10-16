"""Offline checks: metadata URLs, row/column order, border crop, missing tiles."""
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from tour_tiles import Face, expand, manifest_faces, merge_face, har_faces


class TilesTest(unittest.TestCase):
    def test_url_padding(self):
        self.assertEqual(expand('l%l/%s/%0v/t_%v_%h.jpg', 'f', 2, 3, 4), 'l2/f/03/t_3_4.jpg')

    def test_metadata_is_source_of_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'scenes.json'
            xml = '<image tilesize="512"><level tiledimagewidth="2560"><cube url="tiles/panos/a1/%s/l2/%v/l2_%s_%v_%h.jpg"/></level><level tiledimagewidth="1024"><cube url="tiles/panos/a1/%s/l1/%v/l1_%s_%v_%h.jpg"/></level></image>'
            p.write_text(json.dumps({'format': 'campusar-krpano-scenes-v1', 'base_url': 'https://vinuni.edu.vn/virtual-tour/project.xml', 'source_url': 'https://vinuni.edu.vn/virtual-tour/index.html', 'scenes': [{'name': 'a1', 'content': xml}]}))
            groups, skipped = manifest_faces(p, 'highest', None)
            self.assertFalse(skipped)
            self.assertEqual(len(groups), 6)
            self.assertEqual(groups[0].level, 2)
            self.assertEqual(len(groups[0].urls), 25)
            self.assertEqual(groups[0].urls[4, 2], 'https://vinuni.edu.vn/virtual-tour/tiles/panos/a1/f/l2/4/l2_f_4_2.jpg')

    def test_merge_pixels_and_border(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            g = Face('x', 2, 'f', width=3, height=3, tile_size=2, rows=2, cols=2)
            g.directory(out).mkdir(parents=True)
            colors = {(1, 1): (255, 0, 0), (1, 2): (0, 255, 0), (2, 1): (0, 0, 255), (2, 2): (255, 255, 0)}
            for (r, c), color in colors.items():
                Image.new('RGB', (2, 2), color).save(g.directory(out) / f'{r}_{c}.jpg', format='PNG')
            result = merge_face(g, out)
            self.assertEqual(result['status'], 'merged')
            self.assertEqual(result['size'], [3, 3])
            # JPEG is lossy: check dominant channels, not exact byte equality.
            with Image.open(result['path']) as im:
                self.assertGreater(im.getpixel((0, 0))[0], 150)
                self.assertGreater(im.getpixel((2, 0))[1], 150)
            (g.directory(out) / '2_2.jpg').unlink()
            self.assertEqual(merge_face(g, out)['missing'], [(2, 2)])

    def test_har_never_assumes_extent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'x.har'
            p.write_text(json.dumps({'log': {'entries': [{'request': {'url': 'https://vinuni.edu.vn/virtual-tour/tiles/panos/a1/f/l2/1/l2_f_1_1.jpg'}, 'response': {'content': {}}}]}}))
            groups, _ = har_faces([p], 'highest', None, None)
            self.assertIsNone(groups[0].rows)
            with self.assertRaises(ValueError):
                groups[0].expected()


if __name__ == '__main__':
    unittest.main()
