"""
logo_core.py - the download engine behind app.py (Stock Logo Downloader, v6.1).

No tkinter, pandas or numpy needed: requests + pillow (+ resvg-py to render
SVGs). yfinance is used as a backup for Yahoo lists if it is installed.
"""
import csv
import datetime
import io
import json
import os
import re
import shutil
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, ImageChops, features

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA}
GOOD_ENOUGH_PX = 256
RETRY_MISSING_AFTER_DAYS = 3
INDEX_NAME = "logo_index.json"
CSV_NAME = "logo_index.csv"
OVERRIDES_NAME = "overrides.json"
SVG_DIR_NAME = "SVG logos"

STYLES = {"Icon (square)": "icon", "Logo (full)": "logo", "Symbol": "symbol"}
COLORS = {"Full color": "color", "Black": "black", "White": "white"}
COLOR_FOLDERS = {"color": "", "black": "Black logos", "white": "White logos"}
COLOR_SUFFIX = {"color": "", "black": "_black", "white": "_white"}

# market lists: settings key, label stored in the index, title shown in the log
LISTS = [
    ("trending_n", "trending", "Trending today"),
    ("active_n", "most active", "Most active"),
    ("gainers_n", "gainers", "Day gainers"),
    ("losers_n", "losers", "Day losers"),
    ("sp500_n", "S&P 500", "S&P 500"),
    ("ndx_n", "Nasdaq-100", "Nasdaq-100"),
    ("dow_n", "Dow 30", "Dow 30"),
]

JSON_LOCK = threading.RLock()     # every index / overrides read-modify-write


# ---------- small helpers ----------
def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def today_str():
    return datetime.date.today().isoformat()


def month_key():
    return datetime.date.today().strftime("%Y-%m")


def load_brand_usage(path):
    u = _read_json(path)
    if u.get("month") != month_key():
        u = {"month": month_key(), "used": 0}
    return u


def safe_name(s):
    return "".join(c for c in s if c.isalnum() or c in "-_.^=")


_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?"
                        r"(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)+$")


def clean_domain(s):
    """'https://www.Apple.com/us/' -> 'apple.com'; '' if it isn't a website."""
    s = (s or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0].split(":")[0]
    if s.startswith("www."):
        s = s[4:]
    return s if _DOMAIN_RE.match(s) else ""


# second-level parts of country domains: bp.co.uk, bhp.com.au, sony.co.jp ...
_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "edu", "ac", "ne", "or", "go"}
# shared hosts where the "main domain" is the host, not the company
_SHARED_HOSTS = {"github.io", "wixsite.com", "squarespace.com", "blogspot.com",
                 "azurewebsites.net", "herokuapp.com", "q4cdn.com"}


def root_domain(domain):
    """The company's main domain: 'investor.apple.com' -> 'apple.com',
    'ir.bp.co.uk' -> 'bp.co.uk'. Yahoo sometimes lists an investor-relations
    sub-site, whose icon is often the IR vendor's, not the company's."""
    d = (domain or "").lower().strip(".")
    parts = d.split(".")
    if len(parts) <= 2:
        return d
    keep = 3 if (len(parts[-1]) == 2 and parts[-2] in _SECOND_LEVEL) else 2
    root = ".".join(parts[-keep:])
    return d if root in _SHARED_HOSTS else root


def domain_label(domain):
    """'investor.apple.com' -> 'apple'; 'bhp.com.au' -> 'bhp'."""
    return root_domain(domain).split(".")[0]


_CORP_WORDS = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
               "limited", "plc", "sa", "ag", "nv", "se", "llc", "lp", "the", "holdings",
               "holding", "group", "class", "common", "stock", "shares", "ordinary"}


def _words(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower().replace("&", " and "))


def name_matches(title, name, symbol=""):
    """Does a brand title (Simple Icons' 'Delta', a Wikidata label) fit the
    company ('Delta Air Lines, Inc.')? Deliberately generous - it is only
    meant to catch clear mismatches such as 'ABC' for Alphabet."""
    t = _words(title)
    n = [w for w in _words(name) if w not in _CORP_WORDS]
    if not t or not n:
        return True                         # nothing to compare with
    tj, nj = "".join(t), "".join(n)
    if symbol and tj == "".join(_words(symbol)):
        return True                         # 'IBM' for ticker IBM
    if nj.startswith(tj) or tj.startswith(nj):
        return True                         # 'Apple' / 'Apple Inc.'
    if t[0] == n[0] and (len(t) == 1 or len(n) == 1 or t[1] == n[1]):
        return True                         # 'Delta' / 'Delta Air Lines'
    if len(t) > 1 and "".join(w[0] for w in t) == n[0]:
        return True                         # 'General Electric' / 'GE Aerospace'
    if len(n) > 1 and "".join(w[0] for w in n) == tj:
        return True                         # 'IBM' / 'International Business Machines'
    return False


def parse_ticker_entries(text):
    """'aapl, BRK-B=berkshirehathaway.com ES=F' ->
    (['AAPL', 'BRK-B', 'ES=F'], {'BRK-B': 'berkshirehathaway.com'}).
    'TICKER=website' sets the website; Yahoo symbols like ES=F stay tickers."""
    out, domains = [], {}
    for tok in re.split(r"[,\s;]+", text or ""):
        if not tok:
            continue
        dom = ""
        if "=" in tok:
            left, right = tok.split("=", 1)
            d = clean_domain(right)
            if d and left:
                tok, dom = left, d
        t = safe_name(tok.upper())
        if not t:
            continue
        if t not in out:
            out.append(t)
        if dom:
            domains[t] = dom
    return out, domains


def parse_tickers(text):
    return parse_ticker_entries(text)[0]


def library_for(base, color_mode):
    sub = COLOR_FOLDERS.get(color_mode, "")
    return os.path.join(base, sub) if sub else base


def png_path(lib, symbol):
    return os.path.join(lib, f"{safe_name(symbol)}.png")


def svg_path_for(base, symbol, color="color"):
    sub = COLOR_FOLDERS.get(color, "")
    d = os.path.join(base, SVG_DIR_NAME, sub) if sub else os.path.join(base, SVG_DIR_NAME)
    return os.path.join(d, f"{safe_name(symbol)}.svg")


def load_index(lib):
    return _read_json(os.path.join(lib, INDEX_NAME))


def save_index(lib, index):
    _write_json(os.path.join(lib, INDEX_NAME), index)


def export_csv(lib, index):
    try:
        with open(os.path.join(lib, CSV_NAME), "w", newline="",
                  encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "company", "domain", "status", "source",
                        "width_px", "list", "date", "vector", "svg_source"])
            for sym in sorted(index):
                e = index[sym]
                w.writerow([sym, e.get("name", ""), e.get("domain", ""),
                            e.get("status", ""), e.get("source", ""),
                            e.get("width", ""), e.get("list", ""),
                            e.get("date", ""), e.get("vector", ""),
                            e.get("svg_source", "")])
    except Exception:
        pass


def library_count(lib):
    try:
        return sum(1 for f in os.listdir(lib) if f.lower().endswith(".png"))
    except Exception:
        return 0


def already_have(lib, index, symbol):
    if os.path.exists(png_path(lib, symbol)):
        return True
    e = index.get(symbol)
    if e and e.get("status") == "missing":
        try:
            d = datetime.date.fromisoformat(e.get("date", ""))
            return (datetime.date.today() - d).days < RETRY_MISSING_AFTER_DAYS
        except ValueError:
            return False
    return False


# ---------- per-ticker choices the user made (website, wrong sources, ...) ----------
def load_overrides(base):
    return _read_json(os.path.join(base, OVERRIDES_NAME))


def update_override(base, symbol, **changes):
    """Set / clear keys for one ticker in overrides.json. A value of None, '',
    [] or False removes the key. Returns the ticker's new entry."""
    with JSON_LOCK:
        ov = load_overrides(base)
        e = dict(ov.get(symbol) or {})
        for k, v in changes.items():
            if v in (None, "", [], False):
                e.pop(k, None)
            else:
                e[k] = v
        if e:
            ov[symbol] = e
        else:
            ov.pop(symbol, None)
        _write_json(os.path.join(base, OVERRIDES_NAME), ov)
        return e


def source_family(name):
    """Group source names so 'wrong logo' skips the whole provider
    (e.g. both Brandfetch APIs match the same, wrong, company)."""
    n = (name or "").lower()
    for key, fam in (("brandfetch", "Brandfetch"), ("wikidata", "Wikidata"),
                     ("simple icons", "Simple Icons"), ("website", "Website"),
                     ("fmp", "FMP"), ("logo.dev", "logo.dev")):
        if key in n:
            return fam
    return ""


# ---------- black / white versions ----------
# "ink": how much of a pixel survives in a one-colour version. Near-white parts
# of a colourful logo are usually see-through details (a letter cut out of a
# shape), so they become holes instead of being filled in.
_INK_LUT = [255 if v <= 204 else 0 if v >= 235 else round(255 * (235 - v) / 31)
            for v in range(256)]


def _alpha_sum(ch):
    if ch.mode != "L":
        ch = ch.getchannel("A")
    return sum(i * c for i, c in enumerate(ch.histogram()))


def recolor(img, mode, knockout=True):
    """Solid black/white version (keeps shape + transparency). With knockout,
    white details inside the logo stay see-through - unless that would leave
    almost nothing (an all-white logo), then it is a plain silhouette."""
    if mode == "color":
        return img
    img = img.convert("RGBA")
    w, h = img.size
    alpha = img.getchannel("A")
    lo, _hi = alpha.getextrema()
    if lo >= 250:  # fully opaque: detect a plain background and remove it
        px = img.load()
        corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
        bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
        spread = max(max(abs(c[i] - bg[i]) for c in corners) for i in range(3))
        if spread < 40:
            diff = ImageChops.difference(img.convert("RGB"),
                                         Image.new("RGB", (w, h), bg))
            r, g, b = diff.split()
            m = ImageChops.lighter(ImageChops.lighter(r, g), b)
            alpha = m.point(lambda v: min(255, v * 3))
    if knockout:
        r, g, b, _a = img.split()
        m = ImageChops.darker(ImageChops.darker(r, g), b)   # min channel
        kept = ImageChops.multiply(alpha, m.point(_INK_LUT))
        if _alpha_sum(kept) >= 0.03 * max(1, _alpha_sum(alpha)):
            alpha = kept
    fill = (0, 0, 0) if mode == "black" else (255, 255, 255)
    out = Image.new("RGBA", (w, h), fill + (0,))
    out.putalpha(alpha)
    return out


# ---------- networking: retries, per-site pacing, failure counting ----------
_SESSION = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
_SESSION.mount("https://", _adapter)
_SESSION.mount("http://", _adapter)
_TLS = threading.local()
_PACE_LOCK = threading.Lock()
_NEXT_SLOT = {}
_HOST_GAP = {"query.wikidata.org": 0.6, "query1.finance.yahoo.com": 0.2,
             "query2.finance.yahoo.com": 0.2}


def net_fails():
    """Requests that failed even after retries, on THIS thread."""
    return getattr(_TLS, "fails", 0)


def _count_fail():
    _TLS.fails = net_fails() + 1


def _pace(url):
    """Space out requests to the same site, so parallel workers stay polite."""
    host = urlparse(url).netloc.lower()
    gap = _HOST_GAP.get(host, 0.15)
    with _PACE_LOCK:
        now = time.monotonic()
        slot = max(now, _NEXT_SLOT.get(host, 0.0))
        _NEXT_SLOT[host] = slot + gap
    if slot > now:
        time.sleep(slot - now)


def http_get(url, headers=None, params=None, timeout=20, tries=3):
    """GET with retries (timeouts, connection errors, HTTP 429/5xx) and
    exponential back-off. Honours Retry-After. Counts final failures per
    thread so callers can tell 'does not exist' from 'could not reach'."""
    delay = 1.0
    for attempt in range(tries):
        last = attempt == tries - 1
        _pace(url)
        try:
            r = _SESSION.get(url, headers=headers or HEADERS, params=params,
                             timeout=timeout)
        except (requests.ConnectionError, requests.Timeout):
            if last:
                _count_fail()
                raise
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            if last:
                _count_fail()
                return r
            try:
                wait_s = min(10.0, float(r.headers.get("Retry-After", "")))
            except ValueError:
                wait_s = delay
            time.sleep(wait_s)
            delay *= 2
            continue
        return r


# ---------- Yahoo Finance (direct; yfinance used as backup if installed) ----------
def _yf():
    try:
        import yfinance
        return yfinance
    except Exception:
        return None


class Yahoo:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA,
                               "Accept": "application/json,text/plain,*/*"})
        self.crumb = None
        self._lock = threading.Lock()

    def _get_crumb(self, force=False):
        with self._lock:
            if self.crumb and not force:
                return self.crumb
            self.crumb = None
            try:
                for cookie_url in ("https://fc.yahoo.com", "https://finance.yahoo.com/"):
                    self.s.get(cookie_url, timeout=15)
                    r = self.s.get("https://query1.finance.yahoo.com/v1/test/getcrumb",
                                   timeout=15)
                    if r.status_code == 200 and r.text and "<" not in r.text:
                        self.crumb = r.text.strip()
                        break
            except Exception:
                pass
            return self.crumb

    def _get(self, url, params=None):
        params = dict(params or {})
        for attempt in range(2):
            crumb = self._get_crumb(force=attempt == 1)
            if crumb:
                params["crumb"] = crumb
            _pace(url)
            r = self.s.get(url, params=params, timeout=20)
            if r.status_code in (401, 403, 429) and attempt == 0:
                if r.status_code == 429:
                    time.sleep(2)
                continue
            r.raise_for_status()
            return r.json()

    def trending(self):
        data = self._get("https://query1.finance.yahoo.com/v1/finance/trending/US",
                         {"count": 50})
        return [q["symbol"] for q in data["finance"]["result"][0]["quotes"]]

    def screener(self, scr_id, count=250):
        """Yahoo predefined screens: most_actives, day_gainers, day_losers."""
        try:
            data = self._get(
                "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved",
                {"formatted": "false", "scrIds": scr_id, "count": count, "start": 0})
            return [q["symbol"] for q in data["finance"]["result"][0]["quotes"]]
        except Exception:
            yf = _yf()
            if not yf:
                raise
            res = yf.screen(scr_id, count=count)
            return [q["symbol"] for q in res.get("quotes", [])]

    def most_active(self):
        return self.screener("most_actives")

    def info(self, symbol):
        """(company name, domain, quote type e.g. EQUITY / ETF)"""
        try:
            data = self._get(
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
                {"modules": "price,assetProfile", "formatted": "false"})
            res = data["quoteSummary"]["result"][0]
            price, prof = res.get("price", {}), res.get("assetProfile", {})
            name = price.get("shortName") or price.get("longName") or symbol
            domain = root_domain(clean_domain(prof.get("website") or "")) or None
            return name, domain, (price.get("quoteType") or "EQUITY").upper()
        except Exception:
            yf = _yf()
            if yf:
                try:
                    info = yf.Ticker(symbol).info
                    name = info.get("shortName") or info.get("longName") or symbol
                    domain = root_domain(clean_domain(info.get("website") or "")) or None
                    return name, domain, (info.get("quoteType") or "EQUITY").upper()
                except Exception:
                    pass
            return symbol, None, "EQUITY"


# ---------- backup lists (Nasdaq screener, no key needed) ----------
def _num(x):
    try:
        return float(str(x).replace("$", "").replace(",", "").replace("%", "").strip())
    except ValueError:
        return 0.0


NASDAQ_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/",
}
_NASDAQ_CACHE = {"t": 0.0, "rows": []}


def _nasdaq_rows():
    if _NASDAQ_CACHE["rows"] and time.time() - _NASDAQ_CACHE["t"] < 600:
        return _NASDAQ_CACHE["rows"]
    rows = []
    for ex in ("nasdaq", "nyse", "amex"):
        try:
            r = http_get("https://api.nasdaq.com/api/screener/stocks",
                         params={"tableonly": "true", "limit": "10000",
                                 "exchange": ex, "download": "true"},
                         headers=NASDAQ_HEADERS, timeout=30, tries=2)
            if r.status_code == 200:
                rows += (r.json().get("data") or {}).get("rows") or []
        except Exception:
            continue
    if not rows:
        raise RuntimeError("Nasdaq returned no rows")
    _NASDAQ_CACHE.update(t=time.time(), rows=rows)
    return rows


def nasdaq_screen(sort="volume", count=250, min_price=5.0, min_volume=0):
    """US stocks by today's volume ('volume'), % gain ('up') or % loss ('down')."""
    rows = list(_nasdaq_rows())
    if sort == "up":
        rows.sort(key=lambda x: -_num(x.get("pctchange")))
    elif sort == "down":
        rows.sort(key=lambda x: _num(x.get("pctchange")))
    else:
        rows.sort(key=lambda x: -_num(x.get("volume")))
    out = []
    for row in rows:
        sym = str(row.get("symbol", "")).strip().upper().replace("/", "-")
        vol = _num(row.get("volume"))
        if (not sym or re.search(r"[^A-Z0-9.\-]", sym) or sym in out
                or vol <= 0 or vol < min_volume
                or _num(row.get("lastsale")) < min_price):
            continue
        out.append(sym)
        if len(out) >= count:
            break
    return out


def nasdaq_most_active(count=250, min_price=5.0):
    return nasdaq_screen("volume", count, min_price)


# ---------- index lists (S&P 500, Nasdaq-100, Dow 30) ----------
WIKI_UA = "StockLogoDownloader/1.0 (personal logo collection tool)"
DOW30_FALLBACK = ["AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX",
                  "DIS", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
                  "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V",
                  "VZ", "WMT"]


class _TableParser(HTMLParser):
    """Collects every <table> as rows of cell text (footnotes left out)."""

    def __init__(self):
        super().__init__()
        self.tables, self._stack, self._skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            t = {"id": dict(attrs).get("id") or "", "rows": []}
            self._stack.append(t)
            self.tables.append(t)
        elif not self._stack:
            return
        elif tag == "tr":
            self._stack[-1]["rows"].append([])
        elif tag in ("td", "th"):
            rows = self._stack[-1]["rows"]
            if not rows:
                rows.append([])
            rows[-1].append("")
        elif tag == "br":
            self.handle_data(" ")
        elif tag in ("sup", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "table" and self._stack:
            self._stack.pop()
        elif tag in ("sup", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._stack and not self._skip:
            rows = self._stack[-1]["rows"]
            if rows and rows[-1]:
                rows[-1][-1] += data


def wiki_symbols(page):
    """Ticker column of the constituents table on an English Wikipedia page."""
    r = http_get("https://en.wikipedia.org/w/api.php",
                 params={"action": "parse", "page": page, "prop": "text",
                         "format": "json", "formatversion": "2", "redirects": "1"},
                 headers={"User-Agent": WIKI_UA}, timeout=25, tries=2)
    r.raise_for_status()
    p = _TableParser()
    p.feed(r.json()["parse"]["text"])
    for t in sorted(p.tables, key=lambda t: t["id"] != "constituents"):
        rows = [[c.strip() for c in row] for row in t["rows"] if row]
        if len(rows) < 11:
            continue
        head = [c.lower() for c in rows[0]]
        col = next((i for i, h in enumerate(head) if "symbol" in h or "ticker" in h),
                   None)
        if col is None:
            continue
        out = []
        for row in rows[1:]:
            if col < len(row):
                toks = re.findall(r"\b[A-Z][A-Z0-9]*(?:[.\-][A-Z0-9]+)?\b", row[col])
                if toks:
                    s = toks[-1].replace(".", "-")
                    if s not in out:
                        out.append(s)
        if len(out) >= 10:
            return out
    raise RuntimeError(f"no ticker table found on Wikipedia page {page}")


def sp500_symbols(log=print):
    try:
        return wiki_symbols("List_of_S&P_500_companies")
    except Exception as e:
        log(f"  Wikipedia S&P 500 list failed ({type(e).__name__}); trying a backup.")
    r = http_get("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
                 "main/data/constituents.csv", tries=2)
    r.raise_for_status()
    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
        s = (row.get("Symbol") or "").strip().upper().replace(".", "-")
        if s and s not in out:
            out.append(s)
    if not out:
        raise RuntimeError("backup S&P 500 list was empty")
    return out


def nasdaq100_symbols(log=print):
    try:
        r = http_get("https://api.nasdaq.com/api/quote/list-type/nasdaq100",
                     headers=NASDAQ_HEADERS, tries=2)
        data = (r.json() or {}).get("data") or {}
        rows = (data.get("data") or data).get("rows") or []
        out = []
        for row in rows:
            s = str(row.get("symbol", "")).strip().upper().replace(".", "-")
            if s and s not in out:
                out.append(s)
        if len(out) >= 50:
            return out
    except Exception as e:
        log(f"  Nasdaq-100 list from Nasdaq failed ({type(e).__name__}); trying Wikipedia.")
    return wiki_symbols("Nasdaq-100")


def dow30_symbols(log=print):
    try:
        s = wiki_symbols("Dow_Jones_Industrial_Average")
        if 25 <= len(s) <= 35:
            return s
    except Exception as e:
        log(f"  Wikipedia Dow list failed ({type(e).__name__}).")
    log("  Using the built-in Dow 30 list (may be slightly out of date).")
    return list(DOW30_FALLBACK)


# ---------- logo sources ----------
def fetch_image(url, headers=None):
    try:
        r = http_get(url, headers=headers or HEADERS, tries=2)
        if r.status_code == 200 and len(r.content) > 300:
            im = Image.open(io.BytesIO(r.content))
            fmt = im.format
            img = im.convert("RGBA")
            img.src_format = fmt      # what the file REALLY was (PNG, JPEG, ICO...)
            img.raw = r.content       # the untouched original bytes
            return img
    except Exception:
        pass
    return None


def is_real_png(img):
    return getattr(img, "src_format", None) == "PNG"


# ---------- SVG (vector) sources ----------
MAX_SVG_BYTES = 3_000_000
SVG_RETRY_DAYS = 14          # re-check "no SVG exists" after this long
SVG_UNSURE_RETRY_DAYS = 1    # ...but retry tomorrow if the network was flaky
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


def decode_svg_bytes(raw):
    """Bytes from the web -> clean UTF-8 text. Handles .svgz (gzip), UTF-16,
    a BOM, and files whose XML header names a legacy encoding."""
    if raw[:2] == b"\x1f\x8b":                       # gzip-compressed SVG
        try:
            raw = zlib.decompressobj(31).decompress(raw, MAX_SVG_BYTES + 1)
        except zlib.error:
            return None
    if len(raw) > MAX_SVG_BYTES:
        return None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError:
            return None
    else:
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            m = re.match(rb"\s*<\?xml[^>]*encoding\s*=\s*[\"']([A-Za-z0-9_.\-]+)",
                         raw)
            enc = m.group(1).decode("ascii") if m else "latin-1"
            try:
                text = raw.decode(enc)
            except (LookupError, UnicodeDecodeError):
                text = raw.decode("latin-1")
    text = text.lstrip("﻿ \r\n\t")
    # the text is UTF-8 from here on, so make the XML header say so
    return re.sub(r"^(<\?xml[^>]*?\bencoding\s*=\s*)([\"'])[^\"']*\2",
                  r'\1"utf-8"', text, count=1)


_BANNED_TAGS = {"script", "foreignobject", "iframe", "object", "embed",
                "audio", "video"}
_SHAPE_TAGS = {"path", "rect", "circle", "ellipse", "polygon", "polyline",
               "line", "use"}
_TEXT_TAGS = {"text", "tspan", "textpath"}
_EXTERNAL_URL = re.compile(r"@import|javascript:|url\(\s*['\"]?\s*(?:https?:|//)",
                           re.I)
_SAFE_HREF = re.compile(r"data:image/(?:png|jpe?g|gif|webp)[;,]", re.I)
_SAFE_LINK = re.compile(r"(?:https?:|mailto:|[^:]*$)", re.I)   # web link or no scheme


def _local(name):
    return name.rsplit("}", 1)[-1].lower() if isinstance(name, str) else ""


def _parse_svg(text):
    """-> (root_element, None) if `text` is a well-formed, safe SVG, otherwise
    (None, reason). Safe = no scripts, event handlers, foreignObject, entity
    tricks, or references to anything outside the file."""
    if not text or len(text) > MAX_SVG_BYTES:
        return None, "empty or too large"
    low = text.lower()
    if "<!entity" in low:
        return None, "XML entity declaration"
    if "<?xml-stylesheet" in low:
        return None, "external stylesheet"
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except (ET.ParseError, ValueError):
        return None, "not well-formed XML"
    if _local(root.tag) != "svg":
        return None, "root element is not <svg>"
    for el in root.iter():
        tag = _local(el.tag)
        if tag in _BANNED_TAGS:
            return None, f"<{tag}> element"
        if tag == "style" and _EXTERNAL_URL.search(el.text or ""):
            return None, "external style reference"
        for key, val in el.attrib.items():
            k = _local(key)
            if k.startswith("on"):
                return None, f"event handler ({k})"
            # browsers ignore tabs/newlines inside URLs ("java&#9;script:")
            vn = re.sub(r"[\x00-\x20]", "", val)
            if k == "href":
                if vn.startswith("#") or _SAFE_HREF.match(vn):
                    continue               # internal / embedded bitmap
                if tag == "a" and _SAFE_LINK.match(vn):
                    continue               # a plain web link (not javascript: etc.)
                return None, "external reference"
            if _EXTERNAL_URL.search(val) or _EXTERNAL_URL.search(vn):
                return None, "external reference"
    return root, None


def clean_svg(text):
    """Return the SVG text if it is a real, safe SVG, else None."""
    if not text:
        return None
    t = text.lstrip("﻿ \r\n\t")
    root, _why = _parse_svg(t)
    return t if root is not None else None


def grade_svg(text):
    """'good'  = real vector shapes, nothing that depends on the viewer.
    'fair'  = drawn partly with <text> (needs the right fonts installed) or
              mixes in an embedded bitmap - usable, but only if nothing better.
    'poor'  = nothing drawn, or just a bitmap wrapped in an SVG tag."""
    root, _why = _parse_svg(text)
    if root is None:
        return "poor"
    shapes = texts = images = 0
    for el in root.iter():
        tag = _local(el.tag)
        if tag in _SHAPE_TAGS:
            shapes += 1
        elif tag in _TEXT_TAGS:
            texts += 1
        elif tag == "image":
            images += 1
    if shapes == 0 and texts == 0:
        return "poor"
    return "fair" if (texts or images) else "good"


_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _viewbox(root):
    vb = root.get("viewBox") or root.get("viewbox")
    if vb:
        nums = re.findall(_NUM, vb)
        if len(nums) == 4 and float(nums[2]) > 0 and float(nums[3]) > 0:
            return nums
    return None


def svg_size(text):
    """(width, height) used only for the aspect ratio, from viewBox or the
    width/height attributes. None if neither is usable."""
    root, _why = _parse_svg(text)
    if root is None:
        return None
    vb = _viewbox(root)
    if vb:
        return float(vb[2]), float(vb[3])

    def dim(v):
        m = re.match(r"\s*([0-9]*\.?[0-9]+)\s*(?:px|pt|pc|mm|cm|in)?\s*$", v or "")
        return float(m.group(1)) if m else None
    w, h = dim(root.get("width")), dim(root.get("height"))
    return (w, h) if w and h else None


# ----- one-colour SVGs -----
_PAINT_ATTRS = {"fill", "stroke", "stop-color", "color", "flood-color",
                "lighting-color"}
_CSS_PAINT = re.compile(r"(?<![\w-])(fill|stroke|stop-color|color|flood-color|"
                        r"lighting-color)(\s*:\s*)([^;{}!\"]+)", re.I)
_WHITE_NAMES = {"white", "snow", "ivory", "ghostwhite", "whitesmoke", "floralwhite",
                "azure", "mintcream", "honeydew", "aliceblue", "seashell",
                "linen", "oldlace", "lavenderblush", "cornsilk"}


def _rgb_of(v):
    v = v.strip().lower()
    if v in _WHITE_NAMES:
        return (255, 255, 255)
    m = re.fullmatch(r"#([0-9a-f]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            return tuple(int(h[i] * 2, 16) for i in range(3))
        if len(h) in (6, 8):
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        return None
    m = re.fullmatch(r"rgba?\(\s*([\d.]+%?)[\s,]+([\d.]+%?)[\s,]+([\d.]+%?).*\)", v)
    if m:
        out = []
        for p in m.groups():
            try:
                out.append(float(p[:-1]) * 2.55 if p.endswith("%") else float(p))
            except ValueError:
                return None
        return tuple(out)
    return None


def _map_paint(val, knockout):
    """Mask colour for one paint value: white = shows, black = hole."""
    v = val.strip()
    lv = v.lower()
    if not v or lv in ("none", "transparent", "inherit", "currentcolor") \
            or lv.startswith("url("):
        return val
    rgb = _rgb_of(lv)
    if knockout and rgb and min(rgb) >= 230:
        return "#000"
    return "#fff"


def _recolor_css(text, knockout):
    return _CSS_PAINT.sub(lambda m: m.group(1) + m.group(2)
                          + _map_paint(m.group(3), knockout), text)


def _recolor_tree(el, knockout, skip=False):
    tag = _local(el.tag)
    skip = skip or tag in ("mask", "clippath")   # the logo's own masks keep working
    if not skip:
        for k, v in list(el.attrib.items()):
            lk = _local(k)
            if lk in _PAINT_ATTRS:
                el.set(k, _map_paint(v, knockout))
            elif lk == "style":
                el.set(k, _recolor_css(v, knockout))
        if tag == "style" and el.text:
            el.text = _recolor_css(el.text, knockout)
    for ch in el:
        _recolor_tree(ch, knockout, skip)


def mono_svg(svg_text, mode, knockout=True):
    """A solid black or white SVG of the logo that stays a vector. The artwork
    becomes a mask over one coloured rectangle, so drawing order is kept: with
    knockout, white shapes drawn on top become real holes. None if the SVG
    contains a bitmap (then the PNG is recoloured instead)."""
    root, _why = _parse_svg(svg_text)
    if root is None or any(_local(e.tag) == "image" for e in root.iter()):
        return None
    _recolor_tree(root, knockout)

    def q(t):
        return f"{{{SVG_NS}}}{t}"
    new = ET.Element(q("svg"))
    for k in ("width", "height", "preserveAspectRatio"):
        if root.get(k) is not None:
            new.set(k, root.get(k))
    vb = _viewbox(root)
    if vb:
        new.set("viewBox", " ".join(vb))
        x, y, w, h = vb
    else:
        dims = svg_size(svg_text)
        if dims:
            x, y, w, h = "0", "0", str(dims[0]), str(dims[1])
            new.set("viewBox", f"0 0 {w} {h}")
        else:
            x, y, w, h = "-10000", "-10000", "30000", "30000"
    mid = "stocklogos-mono"
    defs = ET.SubElement(new, q("defs"))
    mask = ET.SubElement(defs, q("mask"), {"id": mid, "maskUnits": "userSpaceOnUse",
                                           "x": x, "y": y, "width": w, "height": h})
    g1 = ET.SubElement(mask, q("g"), {"fill": "#fff", "color": "#fff"})
    drop = {"width", "height", "viewbox", "x", "y", "version",
            "preserveaspectratio", "baseprofile"}
    g2 = ET.SubElement(g1, q("g"), {k: v for k, v in root.attrib.items()
                                    if _local(k) not in drop})
    g2.extend(list(root))
    ET.SubElement(new, q("rect"), {"x": x, "y": y, "width": w, "height": h,
                                   "fill": "#000" if mode == "black" else "#fff",
                                   "mask": f"url(#{mid})"})
    return ET.tostring(new, encoding="unicode")


def fetch_svg(url, headers=None):
    try:
        r = http_get(url, headers=headers)
        if r.status_code != 200 or len(r.content) > MAX_SVG_BYTES:
            return None
        if "html" in r.headers.get("Content-Type", "").lower():
            return None
        return clean_svg(decode_svg_bytes(r.content))
    except Exception:
        return None


def render_svg(svg, size):
    """Render SVG to a transparent RGBA image, longest side = `size` px, with
    the logo's own proportions. Needs the resvg-py package; returns None if it
    is missing or the SVG is blank."""
    try:
        import resvg_py
    except Exception:
        return None
    w = h = size
    dims = svg_size(svg)
    if dims:                                  # size by aspect ratio ourselves
        sw, sh = dims
        if sw >= sh:
            h = max(1, round(size * sh / sw))
        else:
            w = max(1, round(size * sw / sh))
    try:
        data = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=w, height=h))
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    if img.getchannel("A").getbbox() is None:      # nothing drawn
        return None
    return img


def write_svg(path, svg):
    """Save SVG as UTF-8 exactly as given (no Windows newline rewriting),
    via a temp file so a crash never leaves a half-written logo."""
    write_bytes(path, svg.encode("utf-8"))


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def save_image(img, path, fmt="PNG", **kw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    img.save(tmp, fmt, **kw)
    os.replace(tmp, path)


def _same_site(domain, site):
    if not domain or not site:
        return False
    d = domain.lower().replace("www.", "")
    s_ = urlparse(site).netloc.lower().replace("www.", "")
    return bool(s_) and (s_ == d or s_.endswith("." + d) or d.endswith("." + s_))


_WD_RANK = {"PreferredRank": 0, "NormalRank": 1}


def pick_wikidata_logo(rows, domain, name=""):
    """Choose one logo URL from Wikidata rows {item, label, site, logo, rank,
    start, end}. Only SVGs. With a website: the logo must belong to a company
    with that website. Without one: the company's name must match and only one
    company may be left (a short ticker like 'BA' is used on many exchanges).
    Skips logos marked as ended/deprecated; prefers 'preferred' rank, then the
    most recently introduced one."""
    rows = [r for r in rows
            if (r.get("logo") or "").lower().split("?")[0].endswith(".svg")]
    if domain:
        rows = [r for r in rows if _same_site(domain, r.get("site"))]
    else:
        if name:
            rows = [r for r in rows
                    if r.get("label") and name_matches(r["label"], name)]
        if len({r.get("item") or r["logo"] for r in rows}) != 1:
            return None
    if not rows:
        return None
    rows = [r for r in rows if not r.get("end")] or rows
    rows.sort(key=lambda r: r.get("start") or "", reverse=True)
    rows.sort(key=lambda r: _WD_RANK.get(r.get("rank"), 1))   # stable: keeps date order
    return rows[0]["logo"]


def wikidata_query(symbol, domain):
    """One SPARQL query that finds the company three ways at once:
    ticker as a plain statement, ticker as a qualifier on its stock-exchange
    listing (how Wikidata usually stores it), and official website."""
    variants = {symbol, symbol.replace("-", "."), symbol.replace("-", "")}
    vals = " ".join('"%s"' % re.sub(r"[^A-Za-z0-9.\-]", "", v) for v in variants)
    anchors = ["{ VALUES ?t { %s } ?item wdt:P249 ?t . }" % vals,
               "{ VALUES ?t { %s } ?item p:P414 ?ex . ?ex pq:P249 ?t . }" % vals]
    d = (domain or "").lower().replace("www.", "")
    if d and re.fullmatch(r"[a-z0-9.\-]+", d):
        urls = " ".join(f"<{sch}://{host}{slash}>"
                        for host in (d, "www." + d)
                        for sch in ("https", "http")
                        for slash in ("/", ""))
        anchors.append("{ VALUES ?w { %s } ?item wdt:P856 ?w . }" % urls)
    return ("SELECT DISTINCT ?item ?label ?site ?logo ?rank ?start ?end WHERE { "
            + " UNION ".join(anchors) +
            " ?item p:P154 ?st . ?st ps:P154 ?logo ; wikibase:rank ?rank . "
            "FILTER(?rank != wikibase:DeprecatedRank) "
            "OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end } "
            "OPTIONAL { ?item wdt:P856 ?site } "
            "OPTIONAL { ?item rdfs:label ?label . FILTER(LANG(?label) = \"en\") } "
            "} LIMIT 300")


def _wikidata_rows(symbol, domain):
    r = http_get("https://query.wikidata.org/sparql",
                 params={"query": wikidata_query(symbol, domain), "format": "json"},
                 headers={"User-Agent": WIKI_UA,
                          "Accept": "application/sparql-results+json"},
                 timeout=25, tries=2)
    if r.status_code != 200:
        return []
    rows = []
    for b in r.json()["results"]["bindings"]:
        g = {k: v.get("value") for k, v in b.items()}
        rows.append({"item": g.get("item"), "label": g.get("label"),
                     "site": g.get("site"), "logo": g.get("logo"),
                     "rank": (g.get("rank") or "").rsplit("#", 1)[-1],
                     "start": g.get("start"), "end": g.get("end")})
    return rows


def wikidata_svg(symbol, domain, name=""):
    """Official logo (SVG) from Wikidata/Wikimedia Commons, found by ticker or
    website in one query, and checked so it is the right company."""
    pick = pick_wikidata_logo(_wikidata_rows(symbol, domain), domain, name)
    if not pick:
        return None
    return fetch_svg(pick.replace("http://", "https://", 1),
                     headers={"User-Agent": WIKI_UA})


def svg_title(svg):
    """Text of the SVG's first <title> (Simple Icons puts the brand name there)."""
    root, _why = _parse_svg(svg)
    if root is None:
        return ""
    for el in root.iter():
        if _local(el.tag) == "title":
            return "".join(el.itertext()).strip()
    return ""


def simple_icon_svg(domain, name="", symbol="", log=None):
    """Brand glyph from the Simple Icons collection. The slug is guessed from
    the company's main domain (investor.apple.com -> apple), so the icon's own
    brand name is checked against the company name before it is used."""
    if not domain:
        return None
    slug = re.sub(r"[^a-z0-9]", "", domain_label(domain))
    if len(slug) < 2:
        return None
    svg = fetch_svg(f"https://cdn.simpleicons.org/{slug}")
    if svg and name:
        title = svg_title(svg)
        if title and not name_matches(title, name, symbol):
            if log:
                log(f"  {symbol}: ignored Simple Icons '{title}' - it does not "
                    f"look like {name}.")
            return None
    return svg


def pick_brand_api_svg(data, style):
    order = [style] + [t for t in ("logo", "symbol", "icon") if t != style]
    best, best_key = None, None
    for logo in data.get("logos", []):
        ltype = logo.get("type")
        type_rank = order.index(ltype) if ltype in order else len(order)
        theme_rank = {None: 0, "light": 1, "dark": 2}.get(logo.get("theme"), 1)
        for f in logo.get("formats", []):
            if f.get("format") != "svg":
                continue
            key = (type_rank, theme_rank)
            if best_key is None or key < best_key:
                best, best_key = f.get("src"), key
    return best


def pick_brand_api_logo(data, style, png_only=False):
    order = [style] + [t for t in ("logo", "symbol", "icon") if t != style]
    best, best_key = None, None
    for logo in data.get("logos", []):
        ltype = logo.get("type")
        type_rank = order.index(ltype) if ltype in order else len(order)
        theme_rank = {None: 0, "light": 1, "dark": 2}.get(logo.get("theme"), 1)
        for f in logo.get("formats", []):
            ok = ("png",) if png_only else ("png", "jpeg", "jpg", "webp")
            if f.get("format") not in ok:
                continue
            key = (type_rank, theme_rank, -(f.get("width") or 0))
            if best_key is None or key < best_key:
                best, best_key = f.get("src"), key
    return best


class IconParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons = []
        self.svgs = []
        self.manifest = None

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = dict(attrs)
        rel = (a.get("rel") or "").lower()
        href = a.get("href")
        if href and "manifest" in rel:
            self.manifest = href
            return
        if not href or "icon" not in rel:
            return
        if (href.lower().split("?")[0].endswith(".svg")
                or "svg" in (a.get("type") or "").lower()):
            self.svgs.append(href)
        size = 0
        sizes = (a.get("sizes") or "").lower()
        if "x" in sizes:
            try:
                size = int(sizes.split("x")[0])
            except ValueError:
                pass
        if "apple-touch" in rel:
            size = (size or 180) + 1000
        self.icons.append((size, href))


# Home pages and manifests are needed twice per ticker (SVG search, then PNG
# search), so they are fetched once and remembered for the rest of the run.
_PAGE_CACHE = {}
_PAGE_LOCK = threading.Lock()


def clear_page_cache():
    with _PAGE_LOCK:
        _PAGE_CACHE.clear()


def _cached(key, fn):
    with _PAGE_LOCK:
        if key in _PAGE_CACHE:
            return _PAGE_CACHE[key]
    val = fn()
    with _PAGE_LOCK:
        if len(_PAGE_CACHE) > 400:
            _PAGE_CACHE.clear()
        _PAGE_CACHE[key] = val
    return val


def manifest_icons(url):
    return list(_cached(("manifest", url), lambda: _manifest_icons(url)))


def _manifest_icons(url):
    """Icons listed in a site's web-app manifest (often 192-512px)."""
    out = []
    try:
        r = http_get(url, timeout=10, tries=2)
        if r.status_code != 200:
            return out
        for ic in r.json().get("icons", []):
            size = 0
            try:
                size = max(int(x) for x in re.findall(r"\d+", ic.get("sizes", "0")))
            except ValueError:
                pass
            if ic.get("src"):
                out.append((size, urljoin(url, ic["src"])))
    except Exception:
        pass
    return [u for _, u in sorted(out, key=lambda x: -x[0])]


def _home_page(domain, scheme):
    """(final url, IconParser) for the site's home page, or (base, None).
    Tries www.<domain> too, since some sites only answer there."""
    return _cached(("home", domain, scheme), lambda: _fetch_home(domain, scheme))


def _fetch_home(domain, scheme):
    hosts = [domain] if domain.startswith("www.") else [domain, "www." + domain]
    for host in hosts:
        try:
            r = http_get(f"{scheme}://{host}/", timeout=10, tries=2)
            if r.status_code == 200:
                parser = IconParser()
                parser.feed(r.text[:200000])
                return r.url, parser
        except Exception:
            pass
    return f"{scheme}://{domain}/", None


def site_icon(domain, scheme="https", png_only=False):
    """Collect every icon the site offers, download the best few, keep the
    one with the most pixels."""
    base, parser = _home_page(domain, scheme)
    cands = []
    if parser:
        cands += [urljoin(base, h) for _, h in sorted(parser.icons, key=lambda x: -x[0])]
        if parser.manifest:
            cands += manifest_icons(urljoin(base, parser.manifest))
    cands += [urljoin(base, "/apple-touch-icon.png"),
              urljoin(base, "/apple-touch-icon-precomposed.png")]
    seen, best = set(), None
    for url in cands:
        if url in seen or url.lower().split("?")[0].endswith(".svg"):
            continue
        seen.add(url)
        if len(seen) > 8:
            break
        img = fetch_image(url)
        if img and png_only and not is_real_png(img):
            continue
        if img and (best is None or img.width > best.width):
            best = img
            if best.width >= 512:
                break
    return best


def site_svg(domain, scheme="https"):
    """The website's own SVG icon (SVG favicon / Safari mask-icon / manifest)."""
    base, parser = _home_page(domain, scheme)
    cands = []
    if parser:
        cands += [urljoin(base, h) for h in parser.svgs]
        if parser.manifest:
            cands += [u for u in manifest_icons(urljoin(base, parser.manifest))
                      if u.lower().split("?")[0].endswith(".svg")]
    cands.append(urljoin(base, "/favicon.svg"))
    seen = set()
    for url in cands:
        if url in seen:
            continue
        seen.add(url)
        if len(seen) > 5:
            break
        svg = fetch_svg(url)
        if svg:
            return svg
    return None


class Fetcher:
    """Tries logo sources in order. get_svg() = vector sources, get() = PNG.
    Safe to use from several worker threads at once."""

    def __init__(self, api_key, client_id, logodev_token, style,
                 monthly_limit, usage, usage_path, log, png_only=True):
        self.api_key = (api_key or "").strip()
        self.client_id = (client_id or "").strip()
        self.logodev_token = (logodev_token or "").strip()
        self.style = style
        self.limit = monthly_limit
        self.usage = usage
        self.usage_path = usage_path
        self.log = log
        self.brand_api_ok = bool(self.api_key)
        self.budget_msg_shown = False
        self.png_only = png_only
        self.rejected = 0          # non-PNG files turned away
        self.warned = set()
        self._bf_cache = {}        # Brand API JSON per symbol (one lookup only)
        self._lock = threading.RLock()

    def _warn_once(self, key, msg):
        with self._lock:
            if key in self.warned:
                return
            self.warned.add(key)
        self.log(msg)

    # ----- Brandfetch Logo API (CDN) -----
    def _bf_get(self, url):
        try:
            r = http_get(url, tries=2)
        except Exception as e:
            self._warn_once("bf-net", "  Brandfetch Logo API: network error "
                            f"({type(e).__name__})")
            return None
        if r.status_code == 200 and "image" not in r.headers.get("Content-Type", ""):
            self._warn_once("bf-html", "  Brandfetch Logo API sent a web page, not an "
                            "image (it blocks scripts). Use the API key or a logo.dev "
                            "token instead.")
            return None
        if r.status_code in (401, 403, 429):
            self._warn_once(f"bf-{r.status_code}", "  Brandfetch Logo API refused: HTTP "
                            f"{r.status_code} - check the Client ID.")
            return None
        return r if r.status_code == 200 else None

    @staticmethod
    def _route(symbol, domain):
        return f"domain/{domain}" if domain else f"ticker/{symbol}"

    def logo_api(self, symbol, domain):
        # ".png" at the end makes Brandfetch send a real PNG (default is WebP)
        r = self._bf_get(f"https://cdn.brandfetch.io/{self._route(symbol, domain)}/w/512/h/512/"
                         f"fallback/404/{self.style}.png?c={self.client_id}")
        if r is None or len(r.content) <= 300:
            return None
        try:
            im = Image.open(io.BytesIO(r.content))
            fmt = im.format
            img = im.convert("RGBA")
            img.src_format, img.raw = fmt, r.content
            return self._accept(img)
        except Exception:
            return None

    def logo_api_svg(self, symbol, domain):
        types = ["symbol", "logo"] if self.style in ("icon", "symbol") else ["logo", "symbol"]
        for t in types:
            r = self._bf_get(f"https://cdn.brandfetch.io/{self._route(symbol, domain)}/"
                             f"fallback/404/{t}.svg?c={self.client_id}")
            if r is not None:
                svg = clean_svg(decode_svg_bytes(r.content))
                if svg:
                    return svg
        return None

    def _accept(self, img):
        """Real-PNG filter: turn away JPEG/ICO/WebP/GIF sources."""
        if img is None:
            return None
        if self.png_only and not is_real_png(img):
            with self._lock:
                self.rejected += 1
            return None
        return img

    # ----- Brandfetch Brand API (key, counted against a budget) -----
    def brand_api_available(self):
        with self._lock:
            if not (self.api_key and self.brand_api_ok):
                return False
            if self.usage["used"] >= self.limit:
                if not self.budget_msg_shown:
                    self.log(f"  Brand API budget reached "
                             f"({self.usage['used']}/{self.limit}). Using backups.")
                    self.budget_msg_shown = True
                return False
            return True

    def brand_api_data(self, symbol, domain):
        with self._lock:
            if symbol in self._bf_cache:
                return self._bf_cache[symbol]
            self.usage["used"] += 1
            _write_json(self.usage_path, self.usage)
        route = f"domain/{domain}" if domain else f"ticker/{symbol}"
        data = None
        try:
            url = f"https://api.brandfetch.io/v2/brands/{route}"
            _pace(url)
            r = _SESSION.get(url, headers={**HEADERS,
                                           "Authorization": f"Bearer {self.api_key}"},
                             timeout=20)
            if r.status_code in (401, 403):
                self.brand_api_ok = False
                self._warn_once("bapi-key", "  Brandfetch API key rejected. Using backups.")
            elif r.status_code == 429:
                self.brand_api_ok = False
                self._warn_once("bapi-429", "  Brandfetch quota reached. Using backups.")
            elif r.status_code == 200:
                data = r.json()
        except Exception:
            data = None
            with self._lock:                 # never reached Brandfetch: don't count it
                self.usage["used"] = max(0, self.usage["used"] - 1)
                _write_json(self.usage_path, self.usage)
        with self._lock:
            self._bf_cache[symbol] = data
        return data

    def brand_api(self, symbol, domain):
        data = self.brand_api_data(symbol, domain)
        if not data:
            return None
        try:
            src = pick_brand_api_logo(data, self.style, self.png_only)
        except Exception:
            return None
        return self._accept(fetch_image(src)) if src else None

    # ----- vector first -----
    def _brand_api_svg(self, symbol, domain):
        data = self.brand_api_data(symbol, domain)
        if not data:
            return None
        try:
            src = pick_brand_api_svg(data, self.style)
        except Exception:
            return None
        return fetch_svg(src) if src else None

    def get_svg(self, symbol, domain, skip=(), name=""):
        """Return (svg_text, source_name) or (None, None).
        Sources are tried in order (providers in `skip` are left out). The first
        'good' SVG (real vector shapes) wins. A 'fair' one (relies on fonts /
        embedded bitmap) is kept as a fallback while the remaining sources are
        tried; a 'poor' one (blank or just a wrapped bitmap) is never used."""
        sources = []
        if self.client_id:
            sources.append(("Brandfetch Logo API (SVG)",
                            lambda: self.logo_api_svg(symbol, domain)))
        if self.brand_api_available():
            sources.append(("Brandfetch Brand API (SVG)",
                            lambda: self._brand_api_svg(symbol, domain)))
        sources += [
            ("Wikidata/Commons SVG", lambda: wikidata_svg(symbol, domain, name)),
            ("Simple Icons SVG", lambda: simple_icon_svg(domain, name, symbol,
                                                         self.log)),
            ("Website SVG icon", lambda: site_svg(domain) if domain else None)]
        fallback = None
        for name, fn in sources:
            if source_family(name) in skip:
                continue
            try:
                svg = fn()
            except Exception:
                svg = None
            if not svg:
                continue
            grade = grade_svg(svg)
            if grade == "good":
                return svg, name
            if grade == "fair":
                if fallback is None:
                    fallback = (svg, name)
            else:
                self.log(f"  {symbol}: ignored {name} - it is only a bitmap "
                         f"inside an SVG tag, or blank.")
        if fallback:
            self.log(f"  {symbol}: only a font/bitmap-based SVG was available "
                     f"({fallback[1]}); text in the PNG may look different if "
                     f"the font is not installed.")
            return fallback
        return None, None

    # ----- PNG sources -----
    def get(self, symbol, domain, skip=()):
        """Return (image, source_name) or (None, None)."""
        if self.client_id and "Brandfetch" not in skip:
            img = self.logo_api(symbol, domain)
            if img:
                return img, "Brandfetch Logo API"
        if "Brandfetch" not in skip and self.brand_api_available():
            img = self.brand_api(symbol, domain)
            if img:
                return img, "Brandfetch Brand API"
        if self.logodev_token and domain and "logo.dev" not in skip:
            img = self._accept(fetch_image(
                f"https://img.logo.dev/{domain}?token={self.logodev_token}"
                f"&size=512&format=png&fallback=404"))
            if img:
                return img, "logo.dev"

        free = []
        if "FMP" not in skip:
            free.append(("FMP", lambda: self._accept(fetch_image(
                f"https://financialmodelingprep.com/image-stock/{symbol}.png"))))
        if domain and "Website" not in skip:
            free.append(("Website icon",
                         lambda: site_icon(domain, png_only=self.png_only)))
        best = None
        for name, fn in free:
            img = fn()
            if not img:
                continue
            if img.width >= GOOD_ENOUGH_PX:
                return img, name
            if best is None or img.width > best[0].width:
                best = (img, name)
        return best if best else (None, None)


# ---------- writing the files ----------
def parse_hex_color(s):
    s = (s or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", s):
        s = "".join(c * 2 for c in s)
    if re.fullmatch(r"[0-9a-fA-F]{6}", s):
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    return None


def parse_sizes(s):
    out = []
    for t in re.findall(r"\d+", str(s or "")):
        n = int(t)
        if 16 <= n <= 8192 and n not in out:
            out.append(n)
    return sorted(out)[:8]


class Output:
    """Turns a logo (image and/or SVG) into the files on disk: colour/black/
    white PNGs, optional black/white SVGs, WebP copies and extra sizes."""

    def __init__(self, base, cfg, log):
        self.base = base
        self.log = log
        self.png_size = max(256, min(8192, int(cfg.get("png_size", 2048) or 2048)))
        self.knockout = bool(cfg.get("knockout", True))
        self.mono_svg_on = bool(cfg.get("mono_svg", True))
        self.trim = bool(cfg.get("trim"))
        self.square = bool(cfg.get("square"))
        try:
            self.pad_pct = max(0.0, min(40.0, float(cfg.get("pad_pct") or 0)))
        except (TypeError, ValueError):
            self.pad_pct = 0.0
        self.bg = parse_hex_color(cfg.get("bg_color"))
        self.webp = bool(cfg.get("webp"))
        if self.webp and not features.check("webp"):
            log("WebP is not available in this Pillow install - skipping .webp copies.")
            self.webp = False
        self.sizes = parse_sizes(cfg.get("extra_sizes"))
        self.processing = bool(self.trim or self.square or self.pad_pct
                               or self.bg is not None)
        self._small_warned = set()
        self._lock = threading.Lock()

    def svg_path(self, symbol, color="color"):
        return svg_path_for(self.base, symbol, color)

    def mono(self, svg, color):
        """Pick the knocked-out version unless it leaves (almost) nothing."""
        k = mono_svg(svg, color, True) if self.knockout else None
        s = mono_svg(svg, color, False)
        if k and s:
            rs = render_svg(s, 256)
            if rs is None:
                return k                      # can't judge (no resvg) - keep details
            rk = render_svg(k, 256)
            if rk is None or _alpha_sum(rk) < 0.03 * max(1, _alpha_sum(rs)):
                return s
            return k
        return k or s

    def finish(self, img):
        img = img.convert("RGBA")
        if self.trim:
            bb = img.getchannel("A").getbbox()
            if bb:
                img = img.crop(bb)
        w, h = img.size
        pad = round(max(w, h) * self.pad_pct / 100)
        if self.square or pad:
            if self.square:
                W = H = max(w, h) + 2 * pad
            else:
                W, H = w + 2 * pad, h + 2 * pad
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            canvas.paste(img, ((W - w) // 2, (H - h) // 2))
            img = canvas
        if self.bg is not None:
            flat = Image.new("RGBA", img.size, self.bg + (255,))
            flat.alpha_composite(img)
            img = flat
        return img

    def write_mono_svgs(self, symbol, svg, colors):
        if not self.mono_svg_on:
            return
        for c in colors:
            if c != "color":
                m = self.mono(svg, c)
                if m:
                    write_svg(self.svg_path(symbol, c), m)

    def write(self, symbol, img, svg, needed, libs):
        """Write the PNG (+ extras) for each colour in `needed`. -> {color: path}"""
        paths = {}
        safe = safe_name(symbol)
        for c in needed:
            vimg = None
            if c == "color":
                vimg = img
            else:
                if svg:
                    m = self.mono(svg, c)
                    if m:
                        if self.mono_svg_on:
                            write_svg(self.svg_path(symbol, c), m)
                        vimg = render_svg(m, self.png_size)
                if vimg is None:
                    vimg = recolor(img, c, self.knockout)
            path = png_path(libs[c], symbol)
            if (c == "color" and not self.processing and is_real_png(img)
                    and getattr(img, "raw", None)):
                write_bytes(path, img.raw)          # byte-for-byte original
                out = img
            else:
                out = self.finish(vimg)
                save_image(out, path)
            if self.webp:
                save_image(out, os.path.join(libs[c], safe + ".webp"), "WEBP",
                           lossless=True)
            for s in self.sizes:
                if max(out.size) < s:
                    with self._lock:
                        first = s not in self._small_warned
                        self._small_warned.add(s)
                    if first:
                        self.log(f"  Note: some logos are smaller than {s}px, so no "
                                 f"{s}px copy was made for them (never upscaled).")
                    continue
                small = out.copy()
                small.thumbnail((s, s), Image.LANCZOS)
                save_image(small, os.path.join(libs[c], f"{s}px", safe + ".png"))
            paths[c] = path
        return paths


# ---------- the job ----------
def run_job(cfg, base, log, status, progress, cancel, item=None):
    """
    cfg keys: <list>_n counts (see LISTS), custom (list of tickers), domains
              ({ticker: website}), skip_existing, zip, api_key, client_id,
              bf_limit, style, color, all_colors, colors (explicit list), min_px,
              logodev_token, png_only, vector, png_size, upgrade_svg, workers,
              include_etfs, knockout, mono_svg, trim, square, pad_pct, bg_color,
              webp, extra_sizes
    base:     root library folder (colour sub-folders are made inside it)
    log(str), status(str), progress(value, maximum): callbacks (value may be
              fractional)
    item(symbol, name, kind, path): optional, called once per logo with kind
              'svg' / 'png' (new), 'svg-added', 'missing' or 'had'
    cancel:   threading.Event
    """
    def emit(symbol, name, kind, path=None):
        if item:
            try:
                item(symbol, name, kind, path)
            except Exception:
                pass

    custom = cfg.get("custom") or []
    skip_existing = cfg.get("skip_existing", True)
    min_px = cfg.get("min_px", 0)
    vector = cfg.get("vector", True)
    include_etfs = bool(cfg.get("include_etfs"))
    try:
        workers = max(1, min(8, int(cfg.get("workers", 4) or 4)))
    except (TypeError, ValueError):
        workers = 4
    out = Output(base, cfg, log)
    clear_page_cache()               # fresh home pages each run
    svg_dir = os.path.join(base, SVG_DIR_NAME)
    upgrade_svg = bool(vector and skip_existing and cfg.get("upgrade_svg", True))
    svg_index_path = os.path.join(svg_dir, "svg_index.json")
    svg_index = _read_json(svg_index_path)
    svg_by_source = Counter()
    upgraded = [0]
    new_svgs = []
    colors = [c for c in (cfg.get("colors") or []) if c in COLOR_FOLDERS] or \
        (list(COLOR_FOLDERS) if cfg.get("all_colors") else [cfg.get("color", "color")])

    for sym, dom in (cfg.get("domains") or {}).items():
        update_override(base, sym, domain=dom)
        log(f"Website for {sym} set to {dom} (remembered for future runs).")
    ov = load_overrides(base)

    usage_path = os.path.join(base, "brandfetch_usage.json")
    usage = load_brand_usage(usage_path)
    limit = cfg.get("bf_limit", 100)
    fetcher = Fetcher(cfg.get("api_key"), cfg.get("client_id"),
                      cfg.get("logodev_token"), cfg.get("style", "icon"),
                      limit, usage, usage_path, log,
                      png_only=cfg.get("png_only", True))
    yahoo = Yahoo()

    libs = {c: library_for(base, c) for c in colors}
    indexes = {}
    for c, lib in libs.items():
        os.makedirs(lib, exist_ok=True)
        indexes[c] = load_index(lib)

    # ----- what to go through -----
    if custom:
        phases = [("custom", "My tickers", len(custom))]
    else:
        phases = []
        for key, label, title in LISTS:
            try:
                n = max(0, int(cfg.get(key, 0) or 0))
            except (TypeError, ValueError):
                n = 0
            if n:
                phases.append((label, title, n))
    maximum = max(1, sum(n for _, _, n in phases))

    seen = set()
    new_files = {c: [] for c in colors}
    by_source = Counter()
    missing = []
    totals = Counter()
    progress(0, maximum)

    # ----- SVG bookkeeping: remember what was searched, so a logo with no SVG
    # is not hunted again on every run -----
    def record_svg(symbol, st, src=""):
        with JSON_LOCK:
            svg_index[symbol] = {"status": st, "source": src, "date": today_str()}
            _write_json(svg_index_path, svg_index)

    def svg_check_due(symbol):
        if os.path.exists(out.svg_path(symbol)):
            return False
        e = svg_index.get(symbol) or {}
        wait_days = {"none": SVG_RETRY_DAYS,
                     "unsure": SVG_UNSURE_RETRY_DAYS}.get(e.get("status"))
        if wait_days is None:
            return True
        try:
            d = datetime.date.fromisoformat(e.get("date", ""))
            return (datetime.date.today() - d).days >= wait_days
        except ValueError:
            return True

    def lookup_svg(symbol, domain, skip, name=""):
        """get_svg + record the outcome. 'unsure' = nothing found but the
        network failed along the way, so it is worth trying again tomorrow."""
        before = net_fails()
        svg, svg_src = fetcher.get_svg(symbol, domain, skip,
                                       "" if name == symbol else name)
        if svg:
            record_svg(symbol, "found", svg_src)
        else:
            record_svg(symbol, "unsure" if net_fails() > before else "none")
        return svg, svg_src

    def add_svg_to_existing(symbol, title):
        """Logo already has a PNG but no SVG: fetch just the SVG. The PNG is
        left untouched (untick 'Skip logos I already have' to re-render it)."""
        if not any(os.path.exists(png_path(libs[c], symbol)) for c in colors):
            return "none"
        ent = next((indexes[c][symbol] for c in colors if symbol in indexes[c]), {})
        name = ent.get("name") or symbol
        domain = root_domain(ent.get("domain") or "") or None   # older runs kept IR sub-sites
        if not ent:                      # library made before the index existed
            name, domain, _qt = yahoo.info(symbol)
        o = ov.get(symbol) or {}
        domain = o.get("domain") or domain
        status(f"{title}: {symbol}  (looking for an SVG)")
        svg, svg_src = lookup_svg(symbol, domain, set(o.get("reject") or []), name)
        if not svg:
            return "none"
        sp = out.svg_path(symbol)
        write_svg(sp, svg)
        out.write_mono_svgs(symbol, svg, [c for c in colors
                                          if os.path.exists(png_path(libs[c], symbol))])
        with JSON_LOCK:
            new_svgs.append(sp)
            svg_by_source[svg_src] += 1
            upgraded[0] += 1
            for c in colors:
                e = indexes[c].get(symbol)
                if e and os.path.exists(png_path(libs[c], symbol)):
                    e["vector"], e["svg_source"] = "yes", svg_src
                    save_index(libs[c], indexes[c])
        log(f"SVG ADDED {symbol}  {name}  [{svg_src}]")
        emit(symbol, name, "svg-added", sp)
        return "added"

    def fetch_one(symbol, label, needed):
        name, domain, qtype = yahoo.info(symbol)
        o = ov.get(symbol) or {}
        if o.get("domain"):
            domain = o["domain"]
        if label != "custom" and not (qtype == "EQUITY"
                                      or (include_etfs and qtype == "ETF")):
            log(f"SKIP     {symbol} (not a stock: {qtype.lower()})")
            return "notstock"
        skip = set(o.get("reject") or [])
        img, src, svg, svg_src = None, None, None, None
        if vector:
            svg, svg_src = lookup_svg(symbol, domain, skip, name)
            if svg:
                img = render_svg(svg, out.png_size)
                if img is not None:
                    img.src_format, img.raw, src = "SVG", None, svg_src
                else:
                    log(f"  {symbol}: SVG found ({svg_src}) but it could not be "
                        f"rendered (is resvg-py installed?) - saving the SVG and "
                        f"trying a PNG source for the PNG.")
        if img is None:
            img, src = fetcher.get(symbol, domain, skip)
        if svg:                                  # keep the scalable original
            sp = out.svg_path(symbol)
            write_svg(sp, svg)
            with JSON_LOCK:
                new_svgs.append(sp)
                svg_by_source[svg_src] += 1
        if img and getattr(img, "src_format", "") != "SVG" and img.width < min_px:
            log(f"TOO SMALL {symbol}  {name}  ({img.width}px < {min_px}px)")
            img = None
        if img:
            paths = out.write(symbol, img, svg, needed, libs)
            with JSON_LOCK:
                for c in needed:
                    indexes[c][symbol] = {
                        "name": name, "domain": domain or "",
                        "status": "downloaded", "source": src,
                        "width": img.width, "list": label, "date": today_str(),
                        "vector": "yes" if svg else "",
                        "svg_source": svg_src if svg else ""}
                    save_index(libs[c], indexes[c])
                    new_files[c].append(paths[c])
                by_source[src] += 1
            log(f"NEW      {symbol}  {name}  [{src}, {img.width}px"
                f"{', +SVG' if svg else ''}]")
            emit(symbol, name, "svg" if svg else "png",
                 out.svg_path(symbol) if svg else paths[needed[0]])
            return "new"
        with JSON_LOCK:
            for c in needed:
                indexes[c][symbol] = {
                    "name": name, "domain": domain or "",
                    "status": "missing", "source": "", "width": "",
                    "list": label, "date": today_str()}
                save_index(libs[c], indexes[c])
            missing.append(symbol)
        emit(symbol, name, "missing")
        log(f"MISSING  {symbol}  {name}"
            f"{'' if domain else '  (no website known - set one in the Library)'}")
        return "missing"

    def safe_call(fn, symbol, *a):
        try:
            return fn(symbol, *a)
        except Exception as e:
            log(f"  {symbol}: error ({type(e).__name__}: {e})")
            return "error"

    def process(symbols, label, title, target, offset):
        """Go down `symbols` until `target` new logos are saved, `workers`
        tickers at a time. Progress moves for every ticker looked at."""
        st = Counter()
        have = []
        cnt = {"new": 0, "consumed": 0, "checked": 0}
        total = max(1, len(symbols))

        def bump():
            frac = max(cnt["new"] / target if target else 1.0, cnt["consumed"] / total)
            progress(offset + target * min(1.0, frac), maximum)

        it = iter(symbols)
        pending = {}
        inflight = 0
        exhausted = False
        with ThreadPoolExecutor(max_workers=workers) as ex:
            while True:
                while (not exhausted and len(pending) < workers
                       and not cancel.is_set() and cnt["new"] + inflight < target):
                    sym = next(it, None)
                    if sym is None:
                        exhausted = True
                        break
                    if sym in seen:
                        cnt["consumed"] += 1
                        continue
                    seen.add(sym)
                    o = ov.get(sym) or {}
                    if o.get("ignore") and label != "custom":
                        log(f"SKIP     {sym} (you chose to skip it)")
                        st["skipped by you"] += 1
                        cnt["consumed"] += 1
                        bump()
                        continue
                    if o.get("locked") and any(os.path.exists(png_path(libs[c], sym))
                                               for c in colors):
                        needed = []           # your own file - never replaced
                    else:
                        with JSON_LOCK:
                            needed = [c for c in colors
                                      if not (skip_existing and
                                              already_have(libs[c], indexes[c], sym))]
                    if not needed:
                        have.append(sym)
                        emit(sym, "", "had")
                        if upgrade_svg and not o.get("locked") and svg_check_due(sym):
                            pending[ex.submit(safe_call, add_svg_to_existing,
                                              sym, title)] = "had"
                        else:
                            cnt["consumed"] += 1
                            bump()
                        continue
                    inflight += 1
                    cnt["checked"] += 1
                    status(f"{title}: {sym}  ({cnt['new']}/{target} new)")
                    pending[ex.submit(safe_call, fetch_one, sym, label, needed)] = "fetch"
                if not pending:
                    break
                done, _rest = wait(list(pending), return_when=FIRST_COMPLETED)
                for f in done:
                    kind = pending.pop(f)
                    res = f.result()
                    if kind == "fetch":
                        inflight -= 1
                        if res == "new":
                            cnt["new"] += 1
                        elif res == "missing":
                            st["no usable logo"] += 1
                        elif res == "notstock":
                            st["not a stock"] += 1
                        else:
                            st["errors"] += 1
                    cnt["consumed"] += 1
                    bump()
        if have:
            log(f"  Already had {len(have)}: {', '.join(have[:15])}"
                f"{'...' if len(have) > 15 else ''}")
        extra = "".join(f", {v} {k}" for k, v in st.items()
                        if k not in ("no usable logo", "not a stock") and v)
        log(f"  [{title}] {cnt['new']} new, {st['no usable logo']} no usable logo, "
            f"{len(have)} already had, {st['not a stock']} not stocks{extra} "
            f"- checked {cnt['checked']} of {len(symbols)} listed.")
        if cnt["new"] < target and not cancel.is_set() and label != "custom":
            log(f"  [{title}] only found {cnt['new']} of {target} wanted: reached "
                f"the end of the list.")
        return cnt["new"]

    def get_list(label, title):
        if label == "trending":
            return yahoo.trending()
        if label in ("most active", "gainers", "losers"):
            scr = {"most active": "most_actives", "gainers": "day_gainers",
                   "losers": "day_losers"}[label]
            try:
                syms = yahoo.screener(scr)
                if syms:
                    return syms
            except Exception as e:
                log(f"Yahoo {title.lower()} list unavailable ({type(e).__name__}: {e}).")
            how = {"most active": "volume", "gainers": "up", "losers": "down"}[label]
            syms = nasdaq_screen(how, min_volume=0 if how == "volume" else 200_000)
            log(f"Using Nasdaq's screener for {title.lower()} instead.")
            return syms
        if label == "S&P 500":
            return sp500_symbols(log)
        if label == "Nasdaq-100":
            return nasdaq100_symbols(log)
        if label == "Dow 30":
            return dow30_symbols(log)
        return []

    offset = 0
    for label, title, n in phases:
        if cancel.is_set():
            break
        log(f"=== {title} ===")
        try:
            if label == "custom":
                syms = custom
            else:
                status(f"Fetching the {title} list...")
                syms = get_list(label, title)
                log(f"Got {len(syms)} tickers.")
            totals[title] = process(syms, label, title, n, offset)
        except Exception as e:
            log(f"Could not use the {title} list: {type(e).__name__}: {e}"
                + ("" if label == "custom" else
                   "  (Typing tickers into 'My tickers' always works.)"))
        offset += n
        progress(offset, maximum)

    zips = []
    for c in colors:
        export_csv(libs[c], indexes[c])
        if cfg.get("zip") and new_files[c]:
            zdir = os.path.join(libs[c], "zips")
            os.makedirs(zdir, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
            zpath = os.path.join(zdir, f"new_logos{COLOR_SUFFIX[c]}_{stamp}.zip")
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for p in new_files[c]:
                    z.write(p, os.path.basename(p))
            zips.append(zpath)

    if cancel.is_set():
        log("Cancelled.")
    if missing:
        log(f"No usable logo for: {', '.join(missing)}")
    if by_source:
        log("Sources used: " + ", ".join(
            f"{k} x{v}" for k, v in by_source.most_common()))
    if fetcher.rejected:
        log(f"Turned away {fetcher.rejected} non-PNG / upscaled-type files "
            f"(real PNG only is on).")
    if svg_by_source:
        log("SVG sources: " + ", ".join(
            f"{k} x{v}" for k, v in svg_by_source.most_common()))
    if upgraded[0]:
        log(f"Added SVGs to {upgraded[0]} logos you already had (their PNGs were "
            f"not changed).")
    log(f"Brand API used: {usage['used']} (limit {limit})")
    progress(maximum, maximum)
    total_new = sum(totals.values())
    parts = ", ".join(f"{k} {v}" for k, v in totals.items())
    status(f"Finished. New: {total_new}{f' ({parts})' if len(totals) > 1 else ''}. "
           f"Library: {library_count(libs[colors[0]])} logos.")
    if new_svgs:
        log(f"SVG originals saved in: {svg_dir}")
    return {"new_files": new_files, "libs": libs, "zips": zips,
            "svgs": new_svgs, "svg_dir": svg_dir}


# ---------- library management (used by the Library view) ----------
def logo_files(base, symbol):
    """Every file this program made for one ticker, in all colours."""
    s = safe_name(symbol)
    files = []
    for c in COLOR_FOLDERS:
        lib = library_for(base, c)
        for ext in (".png", ".webp"):
            p = os.path.join(lib, s + ext)
            if os.path.isfile(p):
                files.append(p)
        try:
            for d in os.listdir(lib):
                p = os.path.join(lib, d, s + ".png")
                if re.fullmatch(r"\d+px", d) and os.path.isfile(p):
                    files.append(p)
        except OSError:
            pass
        sp = svg_path_for(base, symbol, c)
        if os.path.isfile(sp):
            files.append(sp)
    return files


def move_logo_files(base, symbol, folder_name):
    """Move a ticker's files into <base>/<folder_name>/<SYMBOL_time>/ (keeps
    the sub-folder layout). Returns (destination, number moved)."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = os.path.join(base, folder_name, f"{safe_name(symbol)}_{stamp}")
    n = 0
    for p in logo_files(base, symbol):
        target = os.path.join(dest, os.path.relpath(p, base))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(p, target)
        n += 1
    return dest, n


def forget_symbol(base, symbol):
    """Drop a ticker from every index (so the next run looks for it again)."""
    with JSON_LOCK:
        for c in COLOR_FOLDERS:
            lib = library_for(base, c)
            idx = load_index(lib)
            if symbol in idx:
                idx.pop(symbol)
                save_index(lib, idx)
                export_csv(lib, idx)
        sip = os.path.join(base, SVG_DIR_NAME, "svg_index.json")
        si = _read_json(sip)
        if symbol in si:
            si.pop(symbol)
            _write_json(sip, si)


def _entry(base, lib, color, idx, ov, s):
    e = idx.get(s) or {}
    o = ov.get(s) or {}
    has_png = os.path.isfile(png_path(lib, s))
    st = e.get("status") or ("downloaded" if has_png else "")
    if st == "downloaded" and not has_png:
        st = "file gone"
    return {"symbol": s, "name": e.get("name", ""),
            "domain": o.get("domain") or e.get("domain", ""),
            "site_set": bool(o.get("domain")), "status": st,
            "source": e.get("source", ""), "svg_source": e.get("svg_source", ""),
            "width": e.get("width", ""), "list": e.get("list", ""),
            "date": e.get("date", ""), "has_png": has_png,
            "has_svg": os.path.isfile(svg_path_for(base, s, color)),
            "locked": bool(o.get("locked")), "ignore": bool(o.get("ignore")),
            "reject": list(o.get("reject") or [])}


def list_library(base, color):
    lib = library_for(base, color)
    idx = load_index(lib)
    ov = load_overrides(base)
    syms = set(idx)
    try:
        syms |= {f[:-4] for f in os.listdir(lib) if f.lower().endswith(".png")}
    except OSError:
        pass
    return [_entry(base, lib, color, idx, ov, s) for s in sorted(syms)]


def entry_info(base, color, symbol):
    lib = library_for(base, color)
    return _entry(base, lib, color, load_index(lib), load_overrides(base), symbol)


def colors_with(base, symbol):
    return [c for c in COLOR_FOLDERS
            if os.path.isfile(png_path(library_for(base, c), symbol))]


def prepare_retry(base, symbol):
    """'Wrong logo': remember the providers that gave it, archive its files,
    forget it in the indexes. Returns (skipped providers, colours it had)."""
    fams = set()
    for c in COLOR_FOLDERS:
        e = load_index(library_for(base, c)).get(symbol) or {}
        for k in ("source", "svg_source"):
            f = source_family(e.get(k))
            if f:
                fams.add(f)
    had = colors_with(base, symbol)
    old = set((load_overrides(base).get(symbol) or {}).get("reject") or [])
    update_override(base, symbol, reject=sorted(old | fams), locked=None)
    move_logo_files(base, symbol, "Rejected logos")
    forget_symbol(base, symbol)
    return sorted(fams), had


def remove_logo(base, symbol, ignore=False):
    dest, n = move_logo_files(base, symbol, "Removed logos")
    forget_symbol(base, symbol)
    update_override(base, symbol, locked=None, ignore=True if ignore else None)
    return dest, n


def replace_logo(base, cfg, symbol, raw, colors, log=print):
    """Use the user's own PNG/SVG for a ticker. It is locked, so later runs
    never overwrite it. Old files go to 'Replaced logos'."""
    out = Output(base, cfg, log)
    svg = None
    head = raw[:4096].lower()
    if raw[:2] == b"\x1f\x8b" or b"<svg" in head:
        svg = clean_svg(decode_svg_bytes(raw))
        if not svg or grade_svg(svg) == "poor":
            raise ValueError("That SVG can't be used (invalid, unsafe, or blank).")
        img = render_svg(svg, out.png_size)
        if img is None:
            raise ValueError("Could not draw that SVG (is resvg-py installed?).")
        img.src_format, img.raw = "SVG", None
    else:
        try:
            im = Image.open(io.BytesIO(raw))
            fmt = im.format
            img = im.convert("RGBA")
        except Exception:
            raise ValueError("That file isn't a picture this program can read.")
        img.src_format, img.raw = fmt, (raw if fmt == "PNG" else None)
    prev = {}
    for c in COLOR_FOLDERS:
        prev = prev or (load_index(library_for(base, c)).get(symbol) or {})
    move_logo_files(base, symbol, "Replaced logos")
    libs = {c: library_for(base, c) for c in colors}
    if svg:
        write_svg(out.svg_path(symbol), svg)
    out.write(symbol, img, svg, colors, libs)
    with JSON_LOCK:
        for c in colors:
            idx = load_index(libs[c])
            idx[symbol] = {"name": prev.get("name", ""), "domain": prev.get("domain", ""),
                           "status": "downloaded", "source": "My file",
                           "width": img.width, "list": prev.get("list") or "custom",
                           "date": today_str(), "vector": "yes" if svg else "",
                           "svg_source": "My file" if svg else ""}
            save_index(libs[c], idx)
            export_csv(libs[c], idx)
    update_override(base, symbol, locked=True, ignore=None)
    return img.width
