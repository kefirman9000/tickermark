"""Offline tests: the Color setting (Full color / Black / White) is respected.

Regression for: with White selected the program still saved the coloured
original SVG and previewed it. Network, Yahoo and the SVG renderer are faked.
"""
import io
import os
import re
import sys
import tempfile
import threading
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logo_core as core  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


# fake resvg: paints a block in the LAST fill in the SVG (for a black/white SVG
# that is the final coloured rectangle, i.e. the real output colour)
def svg_to_bytes(svg_string, width, height):
    fills = re.findall(r'fill="(#[0-9a-fA-F]{3,6})"', svg_string)
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle([width // 5, height // 5, width * 4 // 5, height * 4 // 5],
                                 fill=fills[-1] if fills else "#000")
    b = io.BytesIO()
    im.save(b, "PNG")
    return list(b.getvalue())


sys.modules["resvg_py"] = types.SimpleNamespace(svg_to_bytes=svg_to_bytes)

COLOR_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
             '<path fill="#e60023" d="M1 1h8v8H1z"/></svg>')
BITMAP_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
              '<image href="data:image/png;base64,AAAA"/><path fill="#e60023" d="M1 1h8v8H1z"/></svg>')
state = {"svg": COLOR_SVG, "lookups": 0}


class FakeYahoo:
    def info(self, s):
        return ("Acme Corp", "acme.com", "EQUITY")


def fake_get_svg(self, sym, dom, skip=None, name=""):
    state["lookups"] += 1
    return state["svg"], "Test SVG"


core.Yahoo = FakeYahoo
core.Fetcher.get_svg = fake_get_svg
core.Fetcher.get = lambda self, *a, **k: (None, None)


def centre(path):
    im = Image.open(path).convert("RGBA")
    return im.getpixel((im.width // 2, im.height // 2))


def run(td, items=None, five=True, **cfg):
    c = {"custom": ["AAA"], "vector": True, "png_size": 256, "min_px": 0,
         "skip_existing": True, "workers": 1}
    c.update(cfg)
    got = [] if items is None else items
    cb = (lambda *a: got.append(a)) if five else (lambda s, n, k, p: got.append((s, n, k, p)))
    core.run_job(c, td, lambda m: None, lambda s: None, lambda v, m: None,
                 threading.Event(), item=cb)
    return got


def files(td):
    return sorted(os.path.relpath(os.path.join(r, f), td)
                  for r, _d, fs in os.walk(td) for f in fs
                  if f.endswith((".png", ".svg")))


def ex(td, rel):
    return os.path.exists(os.path.join(td, rel))


sep = os.sep
ORIG = f"SVG logos{sep}AAA.svg"

# ---------- White only ----------
td = tempfile.mkdtemp()
items = run(td, color="white")
check(not ex(td, ORIG), "white: no coloured original SVG is saved")
check(ex(td, f"SVG logos{sep}White logos{sep}AAA.svg"), "white: white SVG saved")
check(centre(os.path.join(td, "White logos", "AAA.png"))[:3] == (255, 255, 255), "white: PNG is white")
check(len(items) == 1 and items[0][2] == "svg" and items[0][4] == "white"
      and items[0][3].endswith(f"White logos{sep}AAA.svg"),
      f"white: preview is the WHITE logo, tagged white ({items})")
check(not ex(td, "AAA.png"), "white: no full-colour PNG")

# ---------- Black only ----------
td = tempfile.mkdtemp()
items = run(td, color="black")
check(not ex(td, ORIG) and ex(td, f"SVG logos{sep}Black logos{sep}AAA.svg"), "black: only the black SVG")
check(centre(os.path.join(td, "Black logos", "AAA.png"))[:3] == (0, 0, 0), "black: PNG is black")
check(items[0][4] == "black" and "Black logos" in items[0][3], "black: preview is the black logo")

# ---------- Full colour (unchanged behaviour) ----------
td = tempfile.mkdtemp()
items = run(td, color="color")
check(ex(td, ORIG), "color: original SVG saved")
check(centre(os.path.join(td, "AAA.png"))[:3] == (230, 0, 35), "color: PNG is coloured")
check(items[0][4] == "color" and items[0][3].endswith(f"SVG logos{sep}AAA.svg"), "color: preview is the colour SVG")

# ---------- All three ----------
td = tempfile.mkdtemp()
items = run(td, all_colors=True)
check(ex(td, ORIG) and ex(td, f"SVG logos{sep}Black logos{sep}AAA.svg")
      and ex(td, f"SVG logos{sep}White logos{sep}AAA.svg"), "all colours: original + black + white SVGs")
check(items[0][4] == "color", "all colours: preview shows the colour version")
# colour already on disk, white added later -> preview is the new white one
os.remove(os.path.join(td, "White logos", "AAA.png"))
idx = core.load_index(os.path.join(td, "White logos"))
idx.pop("AAA", None)
core.save_index(os.path.join(td, "White logos"), idx)
items = run(td, all_colors=True)
check(items and items[-1][4] == "white", f"re-run adding only white previews white ({[i[2:] for i in items]})")

# ---------- 4-argument callbacks still work ----------
td = tempfile.mkdtemp()
items = run(td, color="white", five=False)
check(len(items) == 1 and items[0][2] == "svg", "old 4-argument item callback still receives events")

# ---------- mono SVG switched off ----------
td = tempfile.mkdtemp()
items = run(td, color="white", mono_svg=False)
check(not any(f.endswith(".svg") for f in files(td)), "white + 'mono SVG' off: no SVG files at all")
check(centre(os.path.join(td, "White logos", "AAA.png"))[:3] == (255, 255, 255), "...but the PNG is still white")
check(items[0][2] == "png" and items[0][4] == "white", "...and the tile previews the white PNG")
state["lookups"] = 0
run(td, color="white", mono_svg=False)
check(state["lookups"] == 0, "...and a re-run does not look for SVGs again")

# ---------- adding SVGs to logos you already have ----------
td = tempfile.mkdtemp()
run(td, color="white", vector=False)          # PNG only (resvg not used)
# vector=False + fetcher.get -> nothing; seed a PNG + index like an old library
lib = os.path.join(td, "White logos")
os.makedirs(lib, exist_ok=True)
Image.new("RGBA", (300, 300), (255, 255, 255, 255)).save(os.path.join(lib, "AAA.png"))
core.save_index(lib, {"AAA": {"name": "Acme Corp", "domain": "acme.com", "status": "downloaded",
                              "source": "old", "width": 300, "list": "custom", "date": core.today_str()}})
state["lookups"] = 0
items = run(td, color="white")
check(ex(td, f"SVG logos{sep}White logos{sep}AAA.svg") and not ex(td, ORIG),
      "upgrade (white): adds the WHITE svg, not the coloured original")
check(items and items[-1][2] == "svg-added" and items[-1][4] == "white", "upgrade: tile tagged white")
check(centre(os.path.join(lib, "AAA.png"))[:3] == (255, 255, 255), "upgrade: existing PNG untouched")
state["lookups"] = 0
run(td, color="white")
check(state["lookups"] == 0, "upgrade: second run does no lookups")

# SVG with a bitmap: no black/white SVG possible -> nothing saved, and not retried every run
td = tempfile.mkdtemp()
lib = os.path.join(td, "White logos")
os.makedirs(lib)
Image.new("RGBA", (300, 300), (255, 255, 255, 255)).save(os.path.join(lib, "AAA.png"))
core.save_index(lib, {"AAA": {"name": "Acme Corp", "domain": "acme.com", "status": "downloaded",
                              "source": "old", "width": 300, "list": "custom", "date": core.today_str()}})
state["svg"], state["lookups"] = BITMAP_SVG, 0
items = run(td, color="white")
check(not any(f.endswith(".svg") and "svg_index" not in f for f in files(td)),
      "bitmap SVG: no coloured file leaks into a white-only library")
check(not [i for i in items if i[2] == "svg-added"], "bitmap SVG: no misleading 'SVG added' tile")
n = state["lookups"]
run(td, color="white")
check(state["lookups"] == n, "bitmap SVG: not looked up again on the next run")
state["svg"] = COLOR_SVG

# ---------- the page must not reuse a previous run's pictures ----------
import app  # noqa: E402
check('"/img/"+it.i+"?r="+st.run' in app.PAGE,
      "tile image URLs carry the run id (no stale pictures after changing Color)")
check('[data-c="white"]' in app.PAGE and '[data-c="black"]' in app.PAGE,
      "white / black tiles get a contrasting backdrop")

print("\nFAILED" if fails else "\nALL PASSED", fails)
sys.exit(1 if fails else 0)
