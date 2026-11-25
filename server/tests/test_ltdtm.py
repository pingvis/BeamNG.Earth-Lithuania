"""Streamed Lithuanian terrain: bounded downloads, projection/grid alignment,
parent reuse, nodata, outages, cache separation and source credits. No network.
"""
import io
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cache
import credits
import dem
import experimental
import imagery
import ltdtm
import netfetch
import revisit

KEY = (14, 9279, 5190)


def height(e, n):
    return 100 + (e - 2660000) * .01 + (n - 7340000) * .02


class Fetcher:
    def register_chain(self, *args):
        pass

    def __init__(self, nodata=False, fail=False):
        self.calls, self.nodata, self.fail = [], nodata, fail

    def get(self, url, **_kw):
        self.calls.append(url)
        if self.fail:
            raise netfetch.HostUnavailable("offline")
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        w, s, e, n = map(float, q['bbox'][0].split(','))
        cols, rows = map(int, q['size'][0].split(','))
        x = w + (np.arange(cols) + .5) * (e-w) / cols
        y = n - (np.arange(rows) + .5) * (n-s) / rows
        a = height(x[None, :], y[:, None]).astype(np.float32)
        if self.nodata:
            a[:, cols // 2:] = -9999
        b = io.BytesIO()
        Image.fromarray(a).save(b, 'TIFF')
        return SimpleNamespace(body=b.getvalue())


class TerrainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_healthy_terrain_does_not_poison_plans_or_reload_ancestors(self):
        d = dem.Dem.__new__(dem.Dem)
        d.ltdtm, d.disk, d.ltdtm_served = ltdtm.Terrain(self.tmp.name, Fetcher()), cache.NONE, set()
        d._local, d._lock = threading.local(), threading.RLock()
        d._lru, d._lru_short, d.mem_tiles = OrderedDict(), {}, 8
        d._count = mock.Mock()
        # Exercise the real fault-marking and ancestor-cache paths. A healthy raster
        # must not make every road/bridge plan incomplete or be reloaded per child.
        d.served = {KEY: dem.MAPTERHORN['name']}
        d._make_tile = lambda key: d._lithuania(key, np.full((512, 512), 8., np.float32))
        d._mirror_get = mock.Mock(return_value=None)
        d._mirror_claim = mock.Mock(return_value=True)
        d._mirror_path = lambda key: os.path.join(self.tmp.name, 'decoded')
        d._mirror_put = mock.Mock(side_effect=lambda key, a: a)
        with mock.patch.object(d, '_load_marked', wraps=d._load_marked) as load:
            with revisit.scope() as build:
                a = d._from_ancestor(KEY[0]+1, KEY[1]*2, KEY[2]*2, KEY[0])
                b = d._from_ancestor(KEY[0]+1, KEY[1]*2+1, KEY[2]*2, KEY[0])
            self.assertTrue(build.complete, 'valid Lithuanian terrain must not fault dependent world plans')
            self.assertEqual(load.call_count, 1, 'siblings must reuse a healthy cached ancestor')
        self.assertIn(KEY, d._lru)
        d._mirror_put.assert_called_once()
        self.assertTrue(np.isfinite(a).all() and np.isfinite(b).all())

    def test_decoded_terrain_shared_between_workers_and_expires_with_source(self):
        net = Fetcher()

        def worker():
            d = dem.Dem(self.tmp.name, fetcher=net)
            d.ltdtm = ltdtm.Terrain(os.path.join(self.tmp.name, 'ltdtm'), net)
            def base(key):
                d.served[key] = dem.MAPTERHORN['name']
                return np.full((512, 512), 8., np.float32)
            d._make_tile0 = mock.Mock(side_effect=base)
            return d

        a = worker()
        first = a._load_tile(KEY).copy()
        self.assertEqual(len(net.calls), 1)
        b = worker()
        with mock.patch.object(b, '_make_tile', side_effect=AssertionError('decoded tile should be reused')):
            second = b._load_tile(KEY).copy()
        np.testing.assert_array_equal(first, second)
        self.assertIn(KEY, b.ltdtm_served, 'cached terrain retains Lithuanian attribution')
        source = Path(a.ltdtm.root) / str(KEY[0]) / str(KEY[1]) / f'{KEY[2]}.tif'
        # A source refresh invalidates the older decoded tile, even while its own TTL is fresh.
        stamp = time.time() + 2
        os.utime(source, (stamp, stamp))
        self.assertIsNone(b._mirror_get(KEY))
        # An expired source also prevents decoded reuse, and a fresh worker fetches it again.
        old = time.time() - ltdtm.MAX_AGE_S - 1
        os.utime(source, (old, old))
        a.ltdtm.tile(*KEY)
        self.assertEqual(len(net.calls), 2, 'the original worker also refreshes its in-memory raster')
        c = worker()
        self.assertIsNone(c._mirror_get(KEY))
        c._load_tile(KEY)
        self.assertEqual(len(net.calls), 2)
        # Packed mirrors own their arrays; .npy mirrors can map files on Windows.
        del a, b, c

    def test_plane_and_parent_seams(self):
        net = Fetcher()
        terrain = ltdtm.Terrain(self.tmp.name, net)
        # Two children crossing a *source parent* boundary, plus one sibling.
        z, x, y = 16, KEY[1] * 4 + 3, KEY[2] * 4 + 1
        for tx in (x, x+1, x-1):
            a, ok = terrain.tile(z, tx, y)
            span = 40075016.68557849 / 2**z
            east = (tx + (np.arange(512)+.5)/512) * span - 20037508.342789244
            north = 20037508.342789244 - (y + (np.arange(512)+.5)/512) * span
            expected = height(east[None, :], north[:, None])
            self.assertTrue(ok.all())
            np.testing.assert_allclose(a, expected, atol=3e-5, rtol=0)
        self.assertEqual(len(net.calls), 2, 'siblings reuse their source parent')
        other = ltdtm.Terrain(self.tmp.name, Fetcher(fail=True))
        b, ok = other.tile(z, x, y)
        self.assertTrue(ok.all(), 'a new process can use the disk cache offline')

    def test_nodata_keeps_global_ground(self):
        terrain = ltdtm.Terrain(self.tmp.name, Fetcher(nodata=True))
        d = dem.Dem.__new__(dem.Dem)
        d.ltdtm, d.disk, d.ltdtm_served = terrain, cache.NONE, set()
        d._local = threading.local()
        d._partial, d._count = mock.Mock(), mock.Mock()
        a = np.full((512, 512), 8., np.float32)
        got = d._lithuania(KEY, a)
        self.assertTrue(np.isfinite(got).all())
        np.testing.assert_array_equal(got[:, 300:], a[:, 300:])
        self.assertGreater(float(got[200, 100]), 8)
        self.assertIn(KEY, d.ltdtm_served)

    def test_outage_is_not_persisted_as_a_finished_world(self):
        net = Fetcher(fail=True)
        terrain = ltdtm.Terrain(self.tmp.name, net)
        d = dem.Dem.__new__(dem.Dem)
        d.ltdtm, d.disk = terrain, cache.NONE
        d._local = threading.local()
        a = np.ones((512, 512), np.float32)
        with revisit.scope() as build:
            self.assertIs(d._lithuania(KEY, a), a)
            self.assertIs(d._lithuania(KEY, a), a)
        self.assertFalse(build.complete, 'an outage must still fault dependent world plans')
        self.assertEqual(len(net.calls), 1, 'an outage is cooled down, not repeatedly fetched')
        self.assertFalse(list(Path(self.tmp.name).rglob('*.tif')))

    def test_rejects_colour_tiffs_and_service_error_pages(self):
        b = io.BytesIO()
        Image.new('RGB', (258, 258)).save(b, 'TIFF')
        for body in (b.getvalue(), b'{"error":{"code":500}}', b'<html>maintenance</html>'):
            with self.assertRaises((ValueError, OSError)):
                ltdtm.decode(body)

    def test_disk_limit_and_cache_namespace(self):
        terrain = ltdtm.Terrain(self.tmp.name, Fetcher())
        with mock.patch.object(ltdtm, 'MAX_BYTES', 300000):
            terrain.tile(*KEY)
            terrain.tile(KEY[0], KEY[1]+1, KEY[2])
        self.assertLessEqual(sum(p.stat().st_size for p in Path(self.tmp.name).rglob('*.tif')), 300000)
        d = dem.Dem.__new__(dem.Dem)
        d.cache_dir, d.hkdtm, d.ltdtm = self.tmp.name, None, None
        normal = d._mirror_path(KEY)
        d.ltdtm = terrain
        self.assertNotEqual(normal, d._mirror_path(KEY))
        self.assertFalse(ltdtm.covers(16, 0, 0))
        self.assertFalse(ltdtm.covers(6, 36, 20))

    def test_imagery_and_setting(self):
        source = imagery.SOURCES['lithuania']
        self.assertIn('layers=show:8', source.url, 'no survey-date overlay')
        self.assertTrue(source.lazy)
        self.assertLess(imagery.DEFAULT_CHAIN.index('lithuania'), imagery.DEFAULT_CHAIN.index('poland'))
        self.assertEqual(experimental.env_for({'expLithuaniaTerrain': True}), {'BNE_LT_TERRAIN': '1'})
        self.assertEqual(experimental.env_for({}), {})
        self.assertIn('5 m', credits.label('dem:ltdtm'))
        self.assertIn('CC BY 4.0', credits.full_label('dem:ltdtm'))


if __name__ == '__main__':
    unittest.main()
