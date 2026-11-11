"""
Stock Logo Downloader - simple version (v6.2).
Double-click Run.bat: a page opens in your browser. No tkinter needed.
Needs Python + requests + pillow + resvg-py (yfinance is used as a backup if present).
"""
import base64
import io
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image

import logo_core as core

VERSION = "6.2"
APPDATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                       "StockLogoDownloader")
SETTINGS = os.path.join(APPDATA, "web_settings.json")
DEFAULTS = {
    "trending_n": 25, "active_n": 25, "gainers_n": 0, "losers_n": 0,
    "sp500_n": 0, "ndx_n": 0, "dow_n": 0, "include_etfs": False,
    "custom": "", "tab": "market", "skip_existing": True,
    "zip": False, "folder": os.path.join(os.path.expanduser("~"), "Documents",
                                         "StockLogos"),
    "client_id": "", "api_key": "", "bf_limit": 100, "logodev_token": "",
    "style": "icon", "color": "color", "all_colors": False, "min_px": 128,
    "png_only": True, "vector": True, "png_size": 2048, "upgrade_svg": True,
    "workers": 4, "knockout": True, "mono_svg": True, "trim": False,
    "square": False, "pad_pct": 0, "bg_color": "", "webp": False, "extra_sizes": "",
}
LIST_KEYS = [k for k, _l, _t in core.LISTS]


def _fresh_state():
    return {"running": False, "status": "Ready.", "progress": [0, 1], "log": [],
            "folder": "", "run": 0, "items": [],
            "counts": {"new": 0, "svg": 0, "missing": 0, "had": 0}}


STATE = _fresh_state()
TILE_FILES = []          # index -> file the page may preview (only these are served)
LOCK = threading.Lock()
CANCEL = threading.Event()
THUMBS = {}              # (path, mtime) -> small PNG bytes
SYM_RE = re.compile(r"[A-Za-z0-9\-_^=][A-Za-z0-9\-_.^=]{0,23}")

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stock Logo Downloader</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%234c7dff'/%3E%3Cpath d='M9 21l5-6 4 4 5-8' fill='none' stroke='white' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<script>try{var t=localStorage.getItem("theme");if(t)document.documentElement.dataset.theme=t}catch(e){}</script>
<style>
:root{
  --bg:#0e1015;--surface:#161922;--surface2:#1d212c;--border:#272c39;--fg:#e8eaf0;--muted:#8d95a6;
  --accent:#5b8cff;--accent-fg:#fff;--ok:#3ecf8e;--warn:#f5a524;--danger:#f0616d;--track:#3a4050;
  --check1:#1b1f29;--check2:#222736;--shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.25);
  color-scheme:dark}
@media (prefers-color-scheme:light){:root:not([data-theme=dark]){
  --bg:#f4f5f8;--surface:#fff;--surface2:#f0f2f6;--border:#e1e4ec;--fg:#181b24;--muted:#667085;
  --accent:#3b6df2;--ok:#0f9d62;--warn:#b76e00;--danger:#d92d3a;--track:#c4c9d6;
  --check1:#fff;--check2:#eef0f5;--shadow:0 1px 2px rgba(16,24,40,.06),0 8px 24px rgba(16,24,40,.07);color-scheme:light}}
:root[data-theme=light]{
  --bg:#f4f5f8;--surface:#fff;--surface2:#f0f2f6;--border:#e1e4ec;--fg:#181b24;--muted:#667085;
  --accent:#3b6df2;--ok:#0f9d62;--warn:#b76e00;--danger:#d92d3a;--track:#c4c9d6;
  --check1:#fff;--check2:#eef0f5;--shadow:0 1px 2px rgba(16,24,40,.06),0 8px 24px rgba(16,24,40,.07);color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 "Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:20px 20px 40px}
.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:36px;height:36px;border-radius:10px;background:var(--accent);display:grid;place-items:center}
h1{font-size:20px;margin:0;font-weight:650;letter-spacing:-.01em}
h1 small{font-size:12px;color:var(--muted);font-weight:500;margin-left:6px}
.top-actions{display:flex;gap:8px}
button,input,select,textarea{font:inherit;color:inherit}
.icon-btn{width:38px;height:38px;border-radius:10px;border:1px solid var(--border);background:var(--surface);cursor:pointer;display:grid;place-items:center;flex:none}
.icon-btn:hover{background:var(--surface2)}
.icon-btn svg{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.layout{display:grid;grid-template-columns:350px 1fr;gap:18px;align-items:start}
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);padding:18px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0 0 12px;font-weight:600}
.seg{display:grid;grid-template-columns:1fr 1fr;background:var(--surface2);border-radius:10px;padding:3px;gap:3px;margin-bottom:16px}
.seg button{border:0;background:none;padding:8px;border-radius:8px;cursor:pointer;color:var(--muted);font-weight:550}
.seg button.on{background:var(--surface);color:var(--fg);box-shadow:0 1px 3px rgba(0,0,0,.25)}
.seg.views{max-width:320px;margin:18px 0 0}
label.f{display:block;font-size:13px;color:var(--muted);margin:12px 0 5px}
input[type=text],input[type=password],input[type=number],input[type=search],select,textarea{width:100%;padding:9px 11px;background:var(--surface2);border:1px solid var(--border);border-radius:9px;outline:none}
input:focus,select:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 25%,transparent)}
textarea{resize:vertical;min-height:84px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:0 10px}
.lists label.f{margin-top:8px}
.switch{position:relative;display:flex;align-items:center;gap:11px;margin:12px 0;cursor:pointer}
.switch input{position:absolute;opacity:0;width:0;height:0}
.track{flex:none;width:38px;height:22px;border-radius:99px;background:var(--track);position:relative;transition:background .15s}
.track::after{content:"";position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.35);transition:transform .15s}
.switch input:checked+.track{background:var(--accent)}
.switch input:checked+.track::after{transform:translateX(16px)}
.switch input:focus-visible+.track{outline:2px solid var(--accent);outline-offset:2px}
.switch small{display:block;color:var(--muted);font-size:12px}
.folder{display:flex;gap:8px}
.btn{padding:10px 16px;border-radius:10px;border:1px solid var(--border);background:var(--surface2);cursor:pointer;font-weight:550}
.btn:hover:not(:disabled){filter:brightness(1.12)}
.btn:disabled{opacity:.45;cursor:default}
.btn.primary{background:var(--accent);border-color:transparent;color:var(--accent-fg)}
.btn.danger{color:var(--danger)}
.btn.danger.armed{background:var(--danger);color:#fff;border-color:transparent}
.btn.small{padding:6px 11px;font-size:13px}
.actions{display:grid;grid-template-columns:1fr auto;gap:8px;margin:18px -18px 0;padding:12px 18px;position:sticky;bottom:0;background:var(--surface);border-top:1px solid var(--border);border-radius:0 0 14px 14px;z-index:2}
.link{background:none;border:0;color:var(--muted);cursor:pointer;padding:4px 0;font-size:13px}
.link:hover{color:var(--fg);text-decoration:underline}
.hint{color:var(--muted);font-size:12px;margin:6px 0 0}
.sub{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:600;margin:18px 0 0;padding-top:16px;border-top:1px solid var(--border)}
.statusline{display:flex;justify-content:space-between;gap:12px;font-weight:550}
.statusline span:last-child{color:var(--muted);font-variant-numeric:tabular-nums;flex:none}
.bar{height:8px;background:var(--surface2);border-radius:99px;overflow:hidden;margin:12px 0}
.bar i{display:block;height:100%;width:0;background:var(--accent);border-radius:99px;transition:width .4s ease}
.bar.busy i{background-image:linear-gradient(90deg,transparent,rgba(255,255,255,.35),transparent);background-size:200px 100%;background-repeat:no-repeat;animation:sweep 1.4s infinite}
@keyframes sweep{from{background-position:-200px 0}to{background-position:calc(100% + 200px) 0}}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{padding:4px 11px;border-radius:99px;background:var(--surface2);font-size:13px;color:var(--muted)}
.chip b{color:var(--fg);font-variant-numeric:tabular-nums;margin-right:4px}
.chip.new b{color:var(--ok)}.chip.miss b{color:var(--warn)}
.gallery{margin-top:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:12px}
.empty{grid-column:1/-1;border:1.5px dashed var(--border);border-radius:14px;padding:44px 20px;text-align:center;color:var(--muted)}
.empty svg{width:34px;height:34px;stroke:currentColor;fill:none;stroke-width:1.5;margin-bottom:8px;opacity:.7}
.tile{display:block;width:100%;padding:0;text-align:left;cursor:pointer;color:inherit;background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;animation:pop .25s ease both}
.tile:hover,.tile:focus-visible{border-color:var(--accent);outline:none}
@keyframes pop{from{opacity:0;transform:scale(.94)}to{opacity:1;transform:none}}
.thumb{position:relative;aspect-ratio:1;display:grid;place-items:center;padding:14px;
  background-color:var(--check1);
  background-image:linear-gradient(45deg,var(--check2) 25%,transparent 25%,transparent 75%,var(--check2) 75%),linear-gradient(45deg,var(--check2) 25%,transparent 25%,transparent 75%,var(--check2) 75%);
  background-size:16px 16px;background-position:0 0,8px 8px}
.thumb img{max-width:100%;max-height:100%;object-fit:contain}
.tile.missing .thumb{font-size:30px;color:var(--muted);background:var(--surface2)}
.badge{position:absolute;top:6px;right:6px;font-size:10.5px;font-weight:650;letter-spacing:.03em;padding:2px 7px;border-radius:99px;background:var(--accent);color:#fff}
.tile.png .badge{background:#5b6478}
.tile.missing .badge{background:var(--warn);color:#fff}
.tile.svg-added .badge,.tile.mine .badge{background:var(--ok)}
.cap{padding:8px 10px 10px;border-top:1px solid var(--border)}
.cap b{display:block;font-size:13px}
.cap small{display:block;color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.logbox{margin-top:18px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}
.logbox summary{cursor:pointer;padding:11px 16px;color:var(--muted);font-size:13px}
.logbox pre{margin:0;padding:12px 16px;border-top:1px solid var(--border);max-height:280px;overflow:auto;font:12px/1.5 Consolas,ui-monospace,monospace;color:var(--muted);white-space:pre-wrap}
.toolbar{display:grid;grid-template-columns:1fr 170px 130px auto;gap:8px;align-items:center;margin-top:14px}
.toolbar .count{color:var(--muted);font-size:13px;white-space:nowrap}
.more{display:block;margin:14px auto 0}
[hidden]{display:none!important}
#scrim{position:fixed;inset:0;background:rgba(0,0,0,.5);opacity:0;pointer-events:none;transition:opacity .2s;z-index:10}
#scrim.on{opacity:1;pointer-events:auto}
#drawer{position:fixed;top:0;right:0;bottom:0;width:min(440px,100%);background:var(--surface);border-left:1px solid var(--border);z-index:11;transform:translateX(100%);transition:transform .25s ease;overflow:auto;padding:20px;visibility:hidden}
#drawer.on{transform:none;visibility:visible}
.dhead{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:6px}
.dhead h2{font-size:18px;margin:0;text-transform:none;letter-spacing:0;color:var(--fg)}
.group{margin-top:22px}
.group>h3{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0 0 4px;font-weight:600}
#saved{color:var(--ok);font-size:13px;opacity:0;transition:opacity .3s;margin-left:auto}
#saved.on{opacity:1}
dialog{border:1px solid var(--border);border-radius:16px;background:var(--surface);color:var(--fg);padding:0;width:min(720px,calc(100% - 24px));box-shadow:var(--shadow)}
dialog::backdrop{background:rgba(0,0,0,.55)}
.dlg{padding:20px}
.muted{color:var(--muted);font-size:13px}
.previews{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0}
.previews figure{margin:0;border:1px solid var(--border);border-radius:12px;overflow:hidden}
.previews .thumb{aspect-ratio:4/3}
.previews .thumb[data-none="1"]::after{content:"None";color:var(--muted);font-size:13px}
.previews figcaption{display:flex;justify-content:space-between;padding:7px 10px;font-size:12px;color:var(--muted);border-top:1px solid var(--border)}
.previews figcaption a{color:var(--accent)}
.facts{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:13px;margin:0}
.facts dt{color:var(--muted)}.facts dd{margin:0;overflow-wrap:anywhere}
.dactions{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.msg{min-height:1.4em;font-size:13px;margin:12px 0 0;color:var(--muted)}
.msg.err{color:var(--danger)}.msg.ok{color:var(--ok)}
@media (max-width:860px){.layout{grid-template-columns:1fr}.toolbar{grid-template-columns:1fr 1fr}.previews{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body>
<div class="wrap">
<header class="top">
 <div class="brand"><div class="logo"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 16l5-6 4 4 7-9"/></svg></div><h1>Stock Logo Downloader<small>v__VERSION__</small></h1></div>
 <div class="top-actions">
  <button class="icon-btn" id="theme" title="Light / dark" aria-label="Switch light or dark"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/></svg></button>
  <button class="icon-btn" id="gear" title="Settings" aria-label="Settings"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5h0a1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg></button>
 </div>
</header>

<div class="layout">
 <section class="card">
  <h2>What to download</h2>
  <div class="seg" role="tablist">
   <button id="tab-market" data-tab="market" role="tab">Market lists</button>
   <button id="tab-tickers" data-tab="tickers" role="tab">My tickers</button>
  </div>
  <div id="pane-market">
   <div class="pair lists">
    <div><label class="f" for="trending_n">Trending</label><input type="number" id="trending_n" min="0" max="50"></div>
    <div><label class="f" for="active_n">Most active</label><input type="number" id="active_n" min="0" max="250"></div>
    <div><label class="f" for="gainers_n">Day gainers</label><input type="number" id="gainers_n" min="0" max="250"></div>
    <div><label class="f" for="losers_n">Day losers</label><input type="number" id="losers_n" min="0" max="250"></div>
    <div><label class="f" for="sp500_n">S&amp;P 500</label><input type="number" id="sp500_n" min="0" max="510"></div>
    <div><label class="f" for="ndx_n">Nasdaq-100</label><input type="number" id="ndx_n" min="0" max="105"></div>
    <div><label class="f" for="dow_n">Dow 30</label><input type="number" id="dow_n" min="0" max="30"></div>
   </div>
   <p class="hint">How many <b>new</b> logos to collect from each list. 0 skips a list.</p>
   <label class="switch"><input type="checkbox" id="include_etfs"><span class="track"></span><span>Include ETFs<small>Otherwise only stocks are taken from the lists</small></span></label>
  </div>
  <div id="pane-tickers" hidden>
   <label class="f" for="custom" style="margin-top:0">Tickers</label>
   <textarea id="custom" placeholder="AAPL, NVDA, TSLA&#10;BRK-B=berkshirehathaway.com"></textarea>
   <p class="hint">Separate with commas or spaces. Add <b>=website</b> to tell it the company's site, e.g. <code>BRK-B=berkshirehathaway.com</code>.</p>
   <button class="btn small" id="import" style="margin-top:8px">Import CSV / watchlist&hellip;</button>
   <input type="file" id="importfile" accept=".csv,.txt,.tsv,text/csv,text/plain" hidden>
   <p class="hint" id="importmsg"></p>
  </div>
  <h3 class="sub">Download options</h3>
  <div class="pair">
   <div><label class="f" for="style">Style</label><select id="style"><option value="icon">Icon (square)</option><option value="logo">Logo (full)</option><option value="symbol">Symbol</option></select></div>
   <div><label class="f" for="color">Color</label><select id="color"><option value="color">Full color</option><option value="black">Black</option><option value="white">White</option></select></div>
  </div>
  <label class="switch"><input type="checkbox" id="all_colors"><span class="track"></span><span>Make all three colors in one run<small>Full color, black and white</small></span></label>
  <label class="switch"><input type="checkbox" id="vector"><span class="track"></span><span>Look for SVG first<small>Saves the scalable SVG and renders the PNG from it</small></span></label>
  <label class="switch"><input type="checkbox" id="upgrade_svg"><span class="track"></span><span>Add SVGs to logos I already have<small>Adds the SVG only; your PNGs are not touched</small></span></label>
  <label class="switch"><input type="checkbox" id="png_only"><span class="track"></span><span>Real PNG files only<small>Rejects JPEG, .ico and upscaled favicons</small></span></label>
  <label class="switch"><input type="checkbox" id="skip_existing"><span class="track"></span><span>Skip logos I already have</span></label>
  <label class="switch"><input type="checkbox" id="zip"><span class="track"></span><span>Also make a ZIP of each run</span></label>
  <div class="pair">
   <div><label class="f" for="png_size">PNG size from SVG (px)</label><input type="number" id="png_size" min="256" max="8192" step="256"></div>
   <div><label class="f" for="min_px" title="Logos narrower than this are rejected">Min. width (px)</label><input type="number" id="min_px" min="0"></div>
  </div>
  <p class="hint">More (padding, extra sizes, keys, speed) under the gear icon.</p>
  <label class="f" for="folder">Save folder</label>
  <div class="folder"><input type="text" id="folder"><button class="btn" id="open">Open</button></div>
  <div class="actions"><button class="btn primary" id="start">Start download</button><button class="btn" id="cancel">Cancel</button></div>
  <button class="link" id="quit" style="margin-top:12px">Quit program</button>
 </section>

 <section>
  <div class="card">
   <div class="statusline"><span id="status">Ready.</span><span id="pct"></span></div>
   <div class="bar" id="bar"><i id="fill"></i></div>
   <div class="chips">
    <span class="chip new"><b id="c-new">0</b>new</span>
    <span class="chip"><b id="c-svg">0</b>with SVG</span>
    <span class="chip"><b id="c-had">0</b>already had</span>
    <span class="chip miss"><b id="c-miss">0</b>no logo</span>
   </div>
  </div>
  <div class="seg views" role="tablist">
   <button id="v-run" class="on" role="tab">This run</button>
   <button id="v-lib" role="tab">Library</button>
  </div>
  <div id="view-run">
   <div class="gallery" id="gallery">
    <div class="empty" id="empty"><svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="1.6"/><path d="M21 16l-5-5-8 8"/></svg><div>Logos appear here as they download. Click one to fix it.</div></div>
   </div>
   <details class="logbox"><summary>Details log</summary><pre id="log"></pre></details>
  </div>
  <div id="view-lib" hidden>
   <div class="toolbar">
    <input type="search" id="q" placeholder="Search ticker, company or website" aria-label="Search">
    <select id="filter" aria-label="Filter">
     <option value="all">All logos</option><option value="svg">With SVG</option>
     <option value="pngonly">PNG only (no SVG)</option><option value="missing">No logo</option>
     <option value="mine">My own files</option><option value="site">Website set by me</option>
     <option value="rejected">Has skipped sources</option><option value="ignored">Skipped tickers</option>
    </select>
    <select id="libcolor" aria-label="Which colour"><option value="color">Full color</option><option value="black">Black</option><option value="white">White</option></select>
    <span class="count" id="libcount"></span>
   </div>
   <div class="gallery" id="libgrid"></div>
   <button class="btn more" id="more" hidden>Show more</button>
  </div>
 </section>
</div>
</div>

<dialog id="dlg" aria-labelledby="d-sym">
 <div class="dlg">
  <div class="dhead"><div><h2 id="d-sym"></h2><div class="muted" id="d-name"></div></div><button class="icon-btn" id="d-close" aria-label="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
  <div class="previews">
   <figure><div class="thumb" id="d-pngbox"><img id="d-png" alt="PNG"></div><figcaption><span>PNG</span><a id="d-png-open" target="_blank" rel="noopener">Open full size</a></figcaption></figure>
   <figure><div class="thumb" id="d-svgbox"><img id="d-svg" alt="SVG"></div><figcaption><span>SVG</span><a id="d-svg-open" target="_blank" rel="noopener">Open</a></figcaption></figure>
  </div>
  <dl class="facts" id="d-facts"></dl>
  <label class="f" for="d-site">Company website</label>
  <div class="folder"><input type="text" id="d-site" placeholder="example.com"><button class="btn" id="d-site-save">Save</button></div>
  <p class="hint">Used to find the right logo. Remembered for future runs.</p>
  <div class="dactions">
   <button class="btn primary" id="d-retry" title="Puts this logo aside and skips the provider that gave it">Wrong logo &ndash; try another source</button>
   <button class="btn" id="d-refetch">Download again</button>
   <button class="btn" id="d-replace">Use my own file&hellip;</button>
   <input type="file" id="d-file" accept=".png,.svg,.svgz,.jpg,.jpeg,.webp,image/*" hidden>
   <button class="btn" id="d-reveal">Show in folder</button>
   <button class="btn danger" id="d-remove">Remove</button>
  </div>
  <p class="hint" id="d-rej"></p>
  <label class="switch"><input type="checkbox" id="d-ignore"><span class="track"></span><span>Skip this ticker in market lists<small>Typing it in "My tickers" still works</small></span></label>
  <p class="msg" id="d-msg" role="status"></p>
 </div>
</dialog>

<div id="scrim"></div>
<aside id="drawer" aria-label="Settings">
 <div class="dhead"><h2>Settings</h2><span id="saved">Saved</span><button class="icon-btn" id="close" aria-label="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
 <p class="hint" style="margin-top:14px">Style, color, SVG and size options are on the main page under <b>Download options</b>.</p>
 <div class="group"><h3>Black &amp; white versions</h3>
  <label class="switch"><input type="checkbox" id="knockout"><span class="track"></span><span>Keep inner details<small>White parts inside a logo become see-through instead of filled in</small></span></label>
  <label class="switch"><input type="checkbox" id="mono_svg"><span class="track"></span><span>Also save black / white SVGs<small>In SVG logos\Black logos and \White logos</small></span></label>
 </div>
 <div class="group"><h3>PNG finishing</h3>
  <label class="switch"><input type="checkbox" id="trim"><span class="track"></span><span>Trim empty edges</span></label>
  <label class="switch"><input type="checkbox" id="square"><span class="track"></span><span>Square canvas<small>Logo centred on a square</small></span></label>
  <div class="pair">
   <div><label class="f" for="pad_pct">Padding (%)</label><input type="number" id="pad_pct" min="0" max="40"></div>
   <div><label class="f" for="bg_color">Background</label><input type="text" id="bg_color" placeholder="transparent or #ffffff"></div>
  </div>
  <label class="f" for="extra_sizes">Extra sizes (px)</label><input type="text" id="extra_sizes" placeholder="e.g. 256, 512, 1024">
  <p class="hint">Saved in sub-folders like 512px\. Never made bigger than the original.</p>
  <label class="switch"><input type="checkbox" id="webp"><span class="track"></span><span>Also save a WebP copy</span></label>
  <p class="hint">Any finishing option means the PNG is re-saved instead of kept byte-for-byte.</p>
 </div>
 <div class="group"><h3>Speed</h3>
  <label class="f" for="workers">Tickers at the same time</label><input type="number" id="workers" min="1" max="8">
  <p class="hint">4 is a good balance. Use 1 if sites start refusing requests.</p>
 </div>
 <div class="group"><h3>Brandfetch &amp; logo.dev</h3>
  <label class="f" for="client_id">Brandfetch Client ID (big free quota)</label><input type="password" id="client_id" autocomplete="off">
  <label class="f" for="api_key">Brandfetch API key (optional, ~100 free)</label><input type="password" id="api_key" autocomplete="off">
  <label class="f" for="bf_limit">Brand API monthly limit</label><input type="number" id="bf_limit" min="0">
  <label class="f" for="logodev_token">logo.dev token (optional)</label><input type="password" id="logodev_token" autocomplete="off">
  <p class="hint">Keys are saved in plain text in %APPDATA%\StockLogoDownloader on this PC.</p>
 </div>
</aside>

<script>
const $=id=>document.getElementById(id);
const F=["trending_n","active_n","gainers_n","losers_n","sp500_n","ndx_n","dow_n","include_etfs","custom","skip_existing","zip","folder","client_id","api_key","bf_limit","logodev_token","style","color","all_colors","min_px","png_only","vector","png_size","upgrade_svg","workers","knockout","mono_svg","trim","square","pad_pct","bg_color","webp","extra_sizes"];
const api=(p,b)=>fetch(p,{method:"POST",headers:{"X-Requested-With":"app","Content-Type":"application/json"},body:JSON.stringify(b||{})});
let tab="market",loaded=false,run=-1,shown=0,wasRunning=false;
function setTab(t){tab=t==="tickers"?"tickers":"market";
 $("tab-market").classList.toggle("on",tab==="market");$("tab-tickers").classList.toggle("on",tab==="tickers");
 $("tab-market").setAttribute("aria-selected",tab==="market");$("tab-tickers").setAttribute("aria-selected",tab==="tickers");
 $("pane-market").hidden=tab!=="market";$("pane-tickers").hidden=tab!=="tickers"}
function fill(s){F.forEach(k=>{const e=$(k);if(e.type==="checkbox")e.checked=!!s[k];else e.value=s[k]??""});setTab(s.tab||(s.custom?"tickers":"market"));$("libcolor").value=s.color||"color"}
function collect(){const o={tab};F.forEach(k=>{const e=$(k);o[k]=e.type==="checkbox"?e.checked:e.value});return o}
document.querySelectorAll("#tab-market,#tab-tickers").forEach(b=>b.onclick=()=>{setTab(b.dataset.tab);save()});
let saveTimer;
function save(){clearTimeout(saveTimer);saveTimer=setTimeout(async()=>{try{const r=await api("/settings",collect());
 if(r.ok){const s=$("saved");s.classList.add("on");setTimeout(()=>s.classList.remove("on"),1400)}}catch(e){}},500)}
F.forEach(k=>{const e=$(k);e.addEventListener(e.type==="checkbox"||e.tagName==="SELECT"?"change":"input",()=>{if(loaded)save()})});
const folder=()=>$("folder").value;
const qs=o=>new URLSearchParams(o).toString();
let ver=Date.now();

/* ---------- import a CSV / watchlist ---------- */
function extractTickers(text){
 const lines=text.split(/\r?\n/).filter(l=>l.trim());if(!lines.length)return[];
 const first=lines[0],delim=first.includes("\t")?"\t":(first.includes(";")&&!first.includes(",")?";":",");
 const split=l=>l.split(delim).map(c=>c.trim().replace(/^"|"$/g,"").trim());
 const head=split(first).map(h=>h.toLowerCase());
 let col=head.findIndex(h=>/^(symbol|ticker|ticker symbol|symbols|tickers)$/.test(h));
 if(col<0)col=head.findIndex(h=>h.includes("symbol")||h.includes("ticker"));
 let rows=lines;if(col>=0)rows=lines.slice(1);else col=0;
 const re=/^[A-Z][A-Z0-9]{0,5}([.\-][A-Z0-9]{1,3})?$/,out=[];
 const add=v=>{v=v.toUpperCase().trim();if(re.test(v)){v=v.replace(".","-");if(!out.includes(v))out.push(v)}};
 rows.forEach(l=>add(split(l)[col]||""));
 if(!out.length)text.split(/[\s,;]+/).forEach(add);
 return out}
$("import").onclick=()=>$("importfile").click();
$("importfile").onchange=async e=>{const f=e.target.files[0];e.target.value="";if(!f)return;
 const syms=extractTickers(await f.text());
 if(!syms.length){$("importmsg").textContent=`No tickers found in ${f.name}.`;return}
 const have=$("custom").value.toUpperCase().split(/[,\s;]+/).map(t=>t.split("=")[0]);
 const add=syms.filter(s=>!have.includes(s));
 $("custom").value=($("custom").value.trim()?$("custom").value.trim()+", ":"")+add.join(", ");
 $("importmsg").textContent=`Added ${add.length} tickers from ${f.name}${syms.length>add.length?` (${syms.length-add.length} were already there)`:""}.`;save()};

/* ---------- this run ---------- */
const BADGE={svg:"SVG",png:"PNG","svg-added":"SVG added",missing:"No logo",mine:"My file"};
function tile(symbol,name,kind,src){
 const a=document.createElement("button");a.type="button";a.className="tile "+kind;
 a.onclick=()=>openDetail(symbol);
 const th=document.createElement("div");th.className="thumb";
 if(!src){th.textContent="?"}else{const im=document.createElement("img");im.src=src;im.alt=symbol;im.loading="lazy";th.appendChild(im)}
 const bd=document.createElement("span");bd.className="badge";bd.textContent=BADGE[kind]||kind;th.appendChild(bd);
 const cap=document.createElement("div");cap.className="cap";
 const b=document.createElement("b");b.textContent=symbol;const s=document.createElement("small");s.textContent=name||"";s.title=name||"";
 cap.append(b,s);a.append(th,cap);return a}
function syncTiles(st){
 if(st.run!==run){run=st.run;shown=0;[...$("gallery").querySelectorAll(".tile")].forEach(n=>n.remove())}
 for(;shown<st.items.length;shown++){const it=st.items[shown];$("gallery").appendChild(tile(it.symbol,it.name,it.kind,it.kind==="missing"?null:"/img/"+it.i))}
 $("empty").style.display=shown?"none":""}
async function poll(){try{const st=await (await fetch("/state")).json();
 if(!loaded){fill(st.settings);loaded=true}
 $("status").textContent=st.status;
 const [v,m]=st.progress,p=m?Math.min(100,Math.round(100*v/m)):0;
 $("fill").style.width=(st.running||v?p:0)+"%";$("pct").textContent=st.running||v?p+"%":"";
 $("bar").classList.toggle("busy",st.running);
 $("c-new").textContent=st.counts.new;$("c-svg").textContent=st.counts.svg;$("c-had").textContent=st.counts.had;$("c-miss").textContent=st.counts.missing;
 syncTiles(st);
 const pre=$("log"),atEnd=pre.scrollTop+pre.clientHeight>=pre.scrollHeight-20;
 pre.textContent=st.log.join("\n");if(atEnd)pre.scrollTop=pre.scrollHeight;
 $("start").disabled=st.running;$("cancel").disabled=!st.running;
 if(wasRunning&&!st.running){ver=Date.now();libData=null;if(!$("view-lib").hidden)loadLib()}
 wasRunning=st.running;
}catch(e){$("status").textContent="Program closed."}}

/* ---------- library ---------- */
let libData=null,libLimit=240;
function setView(v){const lib=v==="lib";$("v-run").classList.toggle("on",!lib);$("v-lib").classList.toggle("on",lib);
 $("view-run").hidden=lib;$("view-lib").hidden=!lib;if(lib&&!libData)loadLib()}
$("v-run").onclick=()=>setView("run");$("v-lib").onclick=()=>setView("lib");
async function loadLib(){$("libcount").textContent="Loading...";
 try{const r=await fetch("/library?"+qs({c:$("libcolor").value,f:folder()}));libData=(await r.json()).items||[]}catch(e){libData=[]}
 libLimit=240;renderLib()}
function matches(it){const q=$("q").value.trim().toLowerCase(),f=$("filter").value;
 if(q&&!(it.symbol.toLowerCase().includes(q)||(it.name||"").toLowerCase().includes(q)||(it.domain||"").toLowerCase().includes(q)))return false;
 switch(f){case"svg":return it.has_svg;case"pngonly":return it.has_png&&!it.has_svg;case"missing":return !it.has_png;
  case"mine":return it.locked;case"site":return it.site_set;case"rejected":return it.reject.length>0;case"ignored":return it.ignore}
 return true}
function renderLib(){const g=$("libgrid");g.textContent="";const c=$("libcolor").value;
 const list=(libData||[]).filter(matches);
 list.slice(0,libLimit).forEach(it=>{const kind=!it.has_png?"missing":it.locked?"mine":it.has_svg?"svg":"png";
  g.appendChild(tile(it.symbol,it.name,kind,it.has_png?"/thumb?"+qs({c,s:it.symbol,f:folder(),v:ver}):null))});
 if(!list.length){const e=document.createElement("div");e.className="empty";e.textContent=libData&&libData.length?"Nothing matches.":"No logos in this folder yet.";g.appendChild(e)}
 $("libcount").textContent=`${list.length} of ${(libData||[]).length}`;
 $("more").hidden=list.length<=libLimit}
$("more").onclick=()=>{libLimit+=240;renderLib()};
let qTimer;$("q").oninput=()=>{clearTimeout(qTimer);qTimer=setTimeout(renderLib,150)};
$("filter").onchange=renderLib;$("libcolor").onchange=loadLib;

/* ---------- detail dialog ---------- */
let cur=null;
const dmsg=(t,cls)=>{const m=$("d-msg");m.textContent=t||"";m.className="msg"+(cls?" "+cls:"")};
async function openDetail(sym){
 const c=$("libcolor").value;dmsg("");$("d-remove").classList.remove("armed");$("d-remove").textContent="Remove";
 let it;try{const r=await fetch("/entry?"+qs({c,s:sym,f:folder()}));it=await r.json();if(!r.ok)throw 0}catch(e){return}
 cur=it;$("d-sym").textContent=it.symbol;$("d-name").textContent=it.name||"";
 const u=t=>"/file?"+qs({c,s:it.symbol,t,f:folder(),v:ver});
 $("d-png").hidden=!it.has_png;$("d-png-open").hidden=!it.has_png;if(it.has_png){$("d-png").src=u("png");$("d-png-open").href=u("png")}
 $("d-svg").hidden=!it.has_svg;$("d-svg-open").hidden=!it.has_svg;if(it.has_svg){$("d-svg").src=u("svg");$("d-svg-open").href=u("svg")}
 $("d-pngbox").dataset.none=it.has_png?"":"1";$("d-svgbox").dataset.none=it.has_svg?"":"1";
 const facts=[["Status",it.status||"-"],["Source",it.source||"-"],["Width",it.width?it.width+" px":"-"],["SVG from",it.svg_source||(it.has_svg?"-":"no SVG")],["List",it.list||"-"],["Date",it.date||"-"]];
 if(it.locked)facts.unshift(["Locked","Your own file - runs never replace it"]);
 const dl=$("d-facts");dl.textContent="";facts.forEach(([k,v])=>{const dt=document.createElement("dt");dt.textContent=k;const dd=document.createElement("dd");dd.textContent=v;dl.append(dt,dd)});
 $("d-site").value=it.domain||"";$("d-ignore").checked=it.ignore;
 $("d-rej").textContent="";
 if(it.reject.length){$("d-rej").append(`Skipping these sources for ${it.symbol}: ${it.reject.join(", ")}. `);
  const b=document.createElement("button");b.className="link";b.textContent="Allow them again";b.onclick=()=>act("reset");$("d-rej").append(b)}
 $("d-retry").disabled=!it.has_png;$("d-reveal").disabled=!it.has_png&&!it.has_svg;$("d-remove").disabled=!it.has_png&&!it.has_svg;
 if(!$("dlg").open)$("dlg").showModal()}
async function act(action,extra){if(!cur)return null;
 const r=await api("/entry/"+action,Object.assign({symbol:cur.symbol,color:$("libcolor").value,folder:folder()},extra||{}));
 let j={};try{j=await r.json()}catch(e){}
 if(!r.ok){dmsg(j.error||"That didn't work.","err");return null}
 ver=Date.now();libData=null;
 if(j.started){$("dlg").close();setView("run");return j}
 if(j.entry)await openDetail(cur.symbol);
 if(j.message)dmsg(j.message,"ok");
 if(!$("view-lib").hidden)loadLib();return j}
$("d-close").onclick=()=>$("dlg").close();
$("dlg").addEventListener("click",e=>{if(e.target===$("dlg"))$("dlg").close()});
$("d-site-save").onclick=()=>act("site",{domain:$("d-site").value});
$("d-site").addEventListener("keydown",e=>{if(e.key==="Enter")act("site",{domain:$("d-site").value})});
$("d-retry").onclick=()=>act("retry");
$("d-refetch").onclick=()=>act("refetch",{domain:$("d-site").value});
$("d-reveal").onclick=()=>act("reveal");
$("d-ignore").onchange=()=>act("ignore",{ignore:$("d-ignore").checked});
$("d-remove").onclick=()=>{const b=$("d-remove");
 if(!b.classList.contains("armed")){b.classList.add("armed");b.textContent="Click again to remove";setTimeout(()=>{b.classList.remove("armed");b.textContent="Remove"},4000);return}
 act("remove",{ignore:$("d-ignore").checked})};
$("d-replace").onclick=()=>$("d-file").click();
$("d-file").onchange=async e=>{const f=e.target.files[0];e.target.value="";if(!f)return;
 if(f.size>30e6){dmsg("That file is too big (30 MB max).","err");return}
 dmsg("Saving your file...");
 const data=await new Promise((ok,bad)=>{const r=new FileReader();r.onload=()=>ok(String(r.result).split(",")[1]||"");r.onerror=bad;r.readAsDataURL(f)});
 act("replace",{data,name:f.name})};

/* ---------- general ---------- */
$("theme").onclick=()=>{const cur=document.documentElement.dataset.theme||(matchMedia("(prefers-color-scheme:light)").matches?"light":"dark");
 const t=cur==="dark"?"light":"dark";document.documentElement.dataset.theme=t;try{localStorage.setItem("theme",t)}catch(e){}};
const drawer=(on)=>{$("drawer").classList.toggle("on",on);$("scrim").classList.toggle("on",on)};
$("gear").onclick=()=>drawer(true);$("close").onclick=()=>drawer(false);$("scrim").onclick=()=>drawer(false);
addEventListener("keydown",e=>{if(e.key==="Escape")drawer(false)});
$("start").onclick=()=>{setView("run");api("/start",collect())};
$("cancel").onclick=()=>api("/cancel");
$("open").onclick=()=>api("/open",{folder:folder()});
$("quit").onclick=async()=>{await api("/quit");document.body.innerHTML="<h3 style='padding:40px;font-family:sans-serif'>Closed. You can close this tab.</h3>"};
setInterval(poll,1000);poll();
</script></body></html>""".replace("__VERSION__", VERSION)


def load_settings():
    s = dict(DEFAULTS)
    s.update(core._read_json(SETTINGS))
    return s


def save_settings(data):
    core._write_json(SETTINGS, {k: data.get(k, DEFAULTS[k]) for k in DEFAULTS})


def to_cfg(f):
    def i(k, d):
        try:
            return max(0, int(float(f.get(k, d))))
        except (TypeError, ValueError):
            return d
    style = f.get("style") if f.get("style") in core.STYLES.values() else "icon"
    color = f.get("color") if f.get("color") in core.COLORS.values() else "color"
    own = f.get("tab", "market") == "tickers"
    tickers, domains = core.parse_ticker_entries(f.get("custom", "")) if own else ([], {})
    cfg = {k: i(k, DEFAULTS[k]) for k in LIST_KEYS}
    cfg.update({
        "custom": tickers, "domains": domains,
        "skip_existing": bool(f.get("skip_existing")), "zip": bool(f.get("zip")),
        "api_key": f.get("api_key", ""), "client_id": f.get("client_id", ""),
        "bf_limit": i("bf_limit", 100), "style": style, "color": color,
        "all_colors": bool(f.get("all_colors")), "min_px": i("min_px", 0),
        "png_only": bool(f.get("png_only")),
        "vector": bool(f.get("vector")), "png_size": i("png_size", 2048),
        "upgrade_svg": bool(f.get("upgrade_svg")),
        "logodev_token": f.get("logodev_token", ""),
        "include_etfs": bool(f.get("include_etfs")),
        "workers": min(8, max(1, i("workers", 4))),
        "knockout": bool(f.get("knockout", True)), "mono_svg": bool(f.get("mono_svg", True)),
        "trim": bool(f.get("trim")), "square": bool(f.get("square")),
        "pad_pct": min(40, i("pad_pct", 0)), "bg_color": f.get("bg_color", ""),
        "webp": bool(f.get("webp")), "extra_sizes": f.get("extra_sizes", ""),
    })
    return cfg


def log(msg):
    with LOCK:
        STATE["log"].append(msg)
        del STATE["log"][:-600]


def set_status(msg):
    with LOCK:
        STATE["status"] = msg


def set_progress(v, m):
    with LOCK:
        STATE["progress"] = [v, m]


def add_item(symbol, name, kind, path):
    """Called by the engine once per logo; feeds the thumbnail grid + counters."""
    with LOCK:
        c = STATE["counts"]
        if kind == "had":
            c["had"] += 1
            return
        if kind == "missing":
            c["missing"] += 1
        else:
            if kind in ("svg", "png"):
                c["new"] += 1
            if kind in ("svg", "svg-added"):
                c["svg"] += 1
        i = len(TILE_FILES)
        TILE_FILES.append(path)
        STATE["items"].append({"i": i, "symbol": symbol, "name": name, "kind": kind})


def work(cfg, base):
    try:
        res = core.run_job(cfg, base, log, set_status, set_progress, CANCEL,
                           item=add_item)
        for z in res["zips"]:
            log(f"ZIP: {z}")
        log(f"Logos are in: {base}")
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}")
        set_status(f"Error: {e}")
    finally:
        with LOCK:
            STATE["running"] = False


def start_job(cfg, base):
    """Start a run in the background. False if one is already running."""
    with LOCK:
        if STATE["running"]:
            return False
        run = STATE["run"] + 1
        STATE.clear()
        STATE.update(_fresh_state())
        STATE.update(running=True, status="Starting...", folder=base, run=run)
        del TILE_FILES[:]
    CANCEL.clear()
    threading.Thread(target=work, args=(cfg, base), daemon=True).start()
    return True


def is_running():
    with LOCK:
        return STATE["running"]


def base_from(folder):
    folder = (folder or "").strip()
    return folder or load_settings().get("folder") or DEFAULTS["folder"]


def valid_symbol(s):
    return bool(s and SYM_RE.fullmatch(s) and core.safe_name(s) == s)


def thumbnail(path):
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return None
    data = THUMBS.get(key)
    if data is None:
        try:
            with Image.open(path) as im:
                im = im.convert("RGBA")
                im.thumbnail((200, 200), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "PNG", optimize=True)
                data = buf.getvalue()
        except Exception:
            return None
        if len(THUMBS) > 4000:
            THUMBS.clear()
        THUMBS[key] = data
    return data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj))

    def _host_ok(self):
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def _file(self, path, cache=False):
        if not path or not os.path.isfile(path):
            return self._send(404, "{}")
        ext = os.path.splitext(path)[1].lower()
        ctype = {".svg": "image/svg+xml", ".png": "image/png"}.get(ext)
        if not ctype:
            return self._send(404, "{}")
        with open(path, "rb") as fh:
            data = fh.read()
        # an SVG shown in <img> can't run scripts; the CSP also covers opening it directly
        self._send(200, data, ctype,
                   {"Content-Security-Policy": "sandbox; default-src 'none'; "
                                               "style-src 'unsafe-inline'; img-src data:"})

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "{}")
        u = urlparse(self.path)
        path = u.path
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8",
                       {"Content-Security-Policy": "default-src 'self'; "
                        "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                        "img-src 'self' data:; frame-ancestors 'none'"})
        elif path == "/state":
            with LOCK:
                st = dict(STATE)
                st["log"] = list(STATE["log"])
                st["items"] = list(STATE["items"])
                st["counts"] = dict(STATE["counts"])
            st["settings"] = load_settings()
            self._send(200, json.dumps(st))
        elif re.fullmatch(r"/img/\d{1,6}", path):
            n = int(path.rsplit("/", 1)[1])
            with LOCK:
                p = TILE_FILES[n] if n < len(TILE_FILES) else None
            self._file(p)
        elif path == "/library":
            c = q.get("c") if q.get("c") in core.COLOR_FOLDERS else "color"
            try:
                items = core.list_library(base_from(q.get("f")), c)
            except Exception:
                items = []
            self._json(200, {"items": items})
        elif path in ("/entry", "/thumb", "/file"):
            c = q.get("c") if q.get("c") in core.COLOR_FOLDERS else "color"
            s = q.get("s", "")
            if not valid_symbol(s):
                return self._send(404, "{}")
            base = base_from(q.get("f"))
            if path == "/entry":
                return self._json(200, core.entry_info(base, c, s))
            pngp = core.png_path(core.library_for(base, c), s)
            if path == "/thumb":
                data = thumbnail(pngp)
                if data is None:
                    return self._send(404, "{}")
                return self._send(200, data, "image/png")
            self._file(core.svg_path_for(base, s, c) if q.get("t") == "svg" else pngp)
        else:
            self._send(404, "{}")

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Requested-With") != "app":
            return self._send(403, "{}")
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 45_000_000:
                return self._json(413, {"error": "That file is too big."})
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, "{}")

        if self.path.startswith("/entry/"):
            return self._entry_action(self.path[len("/entry/"):], data)
        if self.path == "/start":
            cfg = to_cfg(data)
            base = base_from(data.get("folder"))
            if not cfg["custom"] and (data.get("tab") == "tickers"
                                      or sum(cfg[k] for k in LIST_KEYS) < 1):
                set_status("Enter at least one ticker or logo count to fetch.")
                return self._send(400, "{}")
            save_settings(data)
            if not start_job(cfg, base):
                return self._send(409, "{}")
        elif self.path == "/settings":
            save_settings(data)              # fine during a run: it uses its own copy
        elif self.path == "/cancel":
            CANCEL.set()
            set_status("Cancelling (finishing the tickers already started)...")
        elif self.path == "/open":
            folder = (data.get("folder") or "").strip()
            if os.path.isdir(folder) and os.name == "nt":
                os.startfile(folder)
            elif not os.path.isdir(folder):
                set_status("That folder doesn't exist yet - run a download first.")
        elif self.path == "/quit":
            self._send(200, "{}")
            threading.Timer(0.3, lambda: os._exit(0)).start()
            return
        self._send(200, "{}")

    # ----- Library actions -----
    def _entry_action(self, action, data):
        s = data.get("symbol", "")
        if not valid_symbol(s):
            return self._json(400, {"error": "Unknown ticker."})
        base = base_from(data.get("folder"))
        color = data.get("color") if data.get("color") in core.COLOR_FOLDERS else "color"
        settings = load_settings()

        def info():
            return core.entry_info(base, color, s)

        if action == "reveal":
            p = core.png_path(core.library_for(base, color), s)
            if not os.path.isfile(p):
                p = core.svg_path_for(base, s, color)
            if os.name == "nt" and os.path.isfile(p):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
            return self._json(200, {})
        if action == "site":
            raw = (data.get("domain") or "").strip()
            dom = core.clean_domain(raw)
            if raw and not dom:
                return self._json(400, {"error": "That doesn't look like a website "
                                                 "(e.g. example.com)."})
            core.update_override(base, s, domain=dom or None)
            msg = ("Website saved. Use “Download again” to fetch with it."
                   if dom else "Website cleared; Yahoo's will be used.")
            return self._json(200, {"entry": info(), "message": msg})
        if action == "ignore":
            core.update_override(base, s, ignore=bool(data.get("ignore")) or None)
            return self._json(200, {"entry": info(),
                                    "message": "Will be skipped in market lists."
                                    if data.get("ignore") else "No longer skipped."})
        if action == "reset":
            core.update_override(base, s, reject=None)
            return self._json(200, {"entry": info(), "message": "All sources allowed again."})

        if is_running():
            return self._json(409, {"error": "Wait for the current download to finish "
                                             "(or press Cancel) and try again."})

        def run_one(colors):
            cfg = to_cfg(settings)
            cfg.update(custom=[s], domains={}, skip_existing=False, zip=False,
                       colors=colors or None)
            return start_job(cfg, base)

        try:
            if action == "retry":
                fams, had = core.prepare_retry(base, s)
                log_note = ", ".join(fams) or "nothing to skip"
                ok = run_one(had)
                if ok:
                    log(f"{s}: wrong logo put aside in 'Rejected logos'. Skipping: "
                        f"{log_note}.")
                return self._json(200 if ok else 409, {"started": ok})
            if action == "refetch":
                raw = (data.get("domain") or "").strip()
                dom = core.clean_domain(raw)
                if dom:
                    core.update_override(base, s, domain=dom)
                core.update_override(base, s, locked=None)
                ok = run_one(core.colors_with(base, s))
                return self._json(200 if ok else 409, {"started": ok})
            if action == "remove":
                dest, n = core.remove_logo(base, s, ignore=bool(data.get("ignore")))
                return self._json(200, {"message": f"Moved {n} file(s) to {dest}",
                                        "entry": info()})
            if action == "replace":
                raw = base64.b64decode(data.get("data") or "", validate=False)
                if not raw:
                    return self._json(400, {"error": "The file was empty."})
                colors = core.colors_with(base, s) or (
                    list(core.COLOR_FOLDERS) if settings.get("all_colors")
                    else [settings.get("color") if settings.get("color")
                          in core.COLOR_FOLDERS else "color"])
                w = core.replace_logo(base, to_cfg(settings), s, raw, colors, log)
                return self._json(200, {"entry": info(),
                                        "message": f"Using your file ({w}px). It is "
                                                   f"locked so runs won't replace it."})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._json(404, {"error": "Unknown action."})


def selftest():
    """Used by the build: proves the packaged program can draw SVGs, then exits."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 20">'
           '<rect width="10" height="20" fill="#e00"/></svg>')
    img = core.render_svg(svg, 200)
    if img is None or max(img.size) != 200:
        print("SELFTEST FAILED: could not render SVG", img and img.size)
        sys.exit(1)
    print(f"selftest ok - version {VERSION}, rendered {img.size}")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if os.name == "nt":
        os.system(f"title Stock Logo Downloader {VERSION}")
    print(f"Stock Logo Downloader {VERSION} is running at", url)
    print("Your browser should open by itself. If it doesn't, copy the address above")
    print("into your browser. Leave this window open while you use it; press Quit")
    print("on the page (or close this window) when you are done.")
    webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
