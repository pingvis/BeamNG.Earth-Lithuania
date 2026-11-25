"""Opt-in streamed Lithuanian GDR50LT terrain (5 m, not lidar).

Geoportal's public F32 export supplies small Web-Mercator rasters. A z14 parent
is reused by all its finer children; one sample of padding prevents tile seams.
There is no point-cloud processing, national download or new runtime dependency.
The older model's vertical reference has not been independently reconciled with
EGM2008, so BNE_LT_TERRAIN=1 is experimental. Nodata and service failures retain
the global terrain. Cache: at most 64 MiB of raw rasters under dem/ltdtm/v1.
"""
import io
import logging
import math
import os
import threading
import time
import urllib.parse
from collections import OrderedDict

import numpy as np
from PIL import Image

import cache as cachemod
import netfetch
import staging
import xproc

log = logging.getLogger("earthd.ltdtm")
ON = os.environ.get("BNE_LT_TERRAIN", "0") == "1"
SOURCE = "ltdtm"
VERSION = "v1"
URL = "https://www.geoportal.lt/mapproxy/elevation/exportImage"
BOUNDS = (53.89, 20.90, 56.45, 26.85)
MIN_ZOOM, MAX_ZOOM = 12, 14
SIZE, PAD = 256, 1
MAX_BYTES = 64 * 1024 * 1024
MEM_TILES = 32
MAX_AGE_S = 30 * 86400
RETRY_S = 300
ATTRIBUTION = ("GDR50LT terrain (2017) (c) Nacionalinė žemės tarnyba prie Aplinkos ministerijos; "
               "GIS-Centras / geoportal.lt (CC BY 4.0), data changed")


def covers(z, x, y):
    if z < MIN_ZOOM:
        return False
    n = 2 ** z
    west, east = x / n * 360 - 180, (x + 1) / n * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return west < BOUNDS[3] and east > BOUNDS[1] and south < BOUNDS[2] and north > BOUNDS[0]


def tile_url(z, x, y):
    span = 40075016.68557849 / 2 ** z
    half, pad = 20037508.342789244, span / SIZE * PAD
    box = (x * span - half - pad, half - (y + 1) * span - pad,
           (x + 1) * span - half + pad, half - y * span + pad)
    return URL + "?" + urllib.parse.urlencode(dict(
        bbox=",".join(f"{v:.8f}" for v in box), bboxSR=3857, imageSR=3857,
        size=f"{SIZE + 2 * PAD},{SIZE + 2 * PAD}", format="tiff", pixelType="F32",
        interpolation="RSP_BilinearInterpolation", noData=-9999, f="image"))


def decode(body):
    with Image.open(io.BytesIO(body)) as im:
        if im.format != "TIFF" or im.mode != "F" or im.size != (SIZE + 2 * PAD,) * 2:
            raise ValueError("Lithuanian terrain is not a padded float32 TIFF")
        a = np.array(im, dtype=np.float32)
    # Includes the advertised -9999 nodata, NaN/Inf, and any extreme service fills.
    a[~np.isfinite(a) | (a < -100) | (a > 1000)] = np.nan
    return a


class Terrain:
    disk = cachemod.NONE

    def __init__(self, root, fetcher):
        self.root, self.fetcher = os.path.join(root, VERSION), fetcher
        self._mem, self._failed = OrderedDict(), OrderedDict()
        self._lock = threading.Lock()

    def _read(self, path):
        try:
            if time.time() - os.path.getmtime(path) > MAX_AGE_S:
                return None
            with open(path, "rb") as f:
                a = decode(f.read())
            self.disk.used(path)
            return a
        except (OSError, ValueError):
            return None

    def _parent(self, key):
        # A provider instance serialises its short LRU/decode work. The shared
        # host limiter and xproc lock also coordinate the server's other workers.
        with self._lock:
            if key in self._mem:
                self._mem.move_to_end(key)
                return self._mem[key]
            if time.monotonic() < self._failed.get(key, 0):
                raise ValueError("Lithuanian terrain source is cooling down")
            path = os.path.join(self.root, *(str(v) for v in key)) + ".tif"
            held = False
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                a, held = xproc.once(path + ".lock", lambda: self._read(path), 5.0, patient=True)
                if a is None:
                    body = self.fetcher.get(tile_url(*key), kind="demand").body
                    a = decode(body)
                    tmp = staging.tmp_for(path)
                    try:
                        with open(tmp, "wb") as f:
                            f.write(body)
                        staging.replace_cached(tmp, path)
                        self.disk.wrote(path, len(body))
                    finally:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    self._trim()
                self._mem[key] = a
                while len(self._mem) > MEM_TILES:
                    self._mem.popitem(last=False)
                self._failed.pop(key, None)
                return a
            except (netfetch.FetchError, OSError, ValueError):
                self._failed[key] = time.monotonic() + RETRY_S
                while len(self._failed) > MEM_TILES:
                    self._failed.popitem(last=False)
                raise
            finally:
                if held:
                    xproc.release(path + ".lock")

    def _trim(self):
        files = []
        for directory, _dirs, names in os.walk(self.root):
            for name in names:
                if name.endswith(".tif"):
                    path = os.path.join(directory, name)
                    try:
                        st = os.stat(path)
                        files.append((st.st_mtime, st.st_size, path))
                    except OSError:
                        pass
        total = sum(row[1] for row in files)
        for _at, size, path in sorted(files):
            if total <= MAX_BYTES:
                break
            try:
                os.remove(path)
                self.disk.forget(path)
                total -= size
            except OSError:
                pass

    def tile(self, z, x, y):
        az = min(z, MAX_ZOOM)
        scale = 2 ** (z - az)
        ax, ay = x // scale, y // scale
        a = self._parent((az, ax, ay))
        # The source has one extra pixel on each side. Samples stay on the
        # same global pixel grid, including across separate parent requests.
        step = (np.arange(512, dtype=np.float64) + .5) / 512
        u = ((x - ax * scale) + step) / scale * SIZE + PAD - .5
        v = ((y - ay * scale) + step) / scale * SIZE + PAD - .5
        ix, iy = np.floor(u).astype(int), np.floor(v).astype(int)
        fx, fy = (u - ix)[None, :], (v - iy)[:, None]
        h = ((a[np.ix_(iy, ix)] * (1-fx) + a[np.ix_(iy, ix+1)] * fx) * (1-fy)
             + (a[np.ix_(iy+1, ix)] * (1-fx) + a[np.ix_(iy+1, ix+1)] * fx) * fy)
        return h.astype(np.float32), np.isfinite(h)
