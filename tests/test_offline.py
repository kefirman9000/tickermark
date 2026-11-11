"""Offline tests for the v6.1 changes (network is faked)."""
import os
import sys
import tempfile
import threading

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logo_core as core  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


# ---- root_domain / domain_label ----
for d, want in [("investor.apple.com", "apple.com"), ("apple.com", "apple.com"),
                ("ir.bp.co.uk", "bp.co.uk"), ("bhp.com.au", "bhp.com.au"),
                ("abc.xyz", "abc.xyz"), ("x.github.io", "x.github.io"),
                ("about.meta.com", "meta.com"), ("", "")]:
    check(core.root_domain(d) == want, f"root_domain({d!r}) -> {core.root_domain(d)!r}")
check(core.domain_label("investors.bhp.com.au") == "bhp", "domain_label bhp")

# ---- name_matches ----
yes = [("Apple", "Apple Inc."), ("Delta", "Delta Air Lines, Inc."),
       ("IBM", "International Business Machines Corporation"),
       ("General Electric", "GE Aerospace"), ("Visa", "Visa Inc."),
       ("NVIDIA", "NVIDIA Corporation"), ("Bank of America", "Bank of America Corporation"),
       ("Google", "GOOGL"), ("Apple Inc.", "Apple Inc."), ("AT&T", "AT&T Inc.")]
no = [("ABC", "Alphabet Inc."), ("American Express", "American Airlines Group Inc."),
      ("Gap", "Goldman Sachs Group Inc."), ("Shell", "Chevron Corporation")]
for t, n in yes:
    check(core.name_matches(t, n), f"match   {t!r} ~ {n!r}")
for t, n in no:
    check(not core.name_matches(t, n), f"reject  {t!r} !~ {n!r}")
check(core.name_matches("IBM", "Something Else", "IBM"), "ticker equals title")

# ---- SVG safety ----
OK = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0h10v10z"/></svg>'
check(core.clean_svg(OK) is not None, "plain SVG accepted")
bad = {
    "a javascript": '<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><path d="M0 0h1"/></a></svg>',
    "a java&#9;script": '<svg xmlns="http://www.w3.org/2000/svg"><a href="java&#9;script:alert(1)"><path d="M0 0h1"/></a></svg>',
    "a data:text/html": '<svg xmlns="http://www.w3.org/2000/svg"><a href="data:text/html,x"><path d="M0 0h1"/></a></svg>',
    "set to= tab js": '<svg xmlns="http://www.w3.org/2000/svg"><a><set attributeName="href" to="java&#x09;script:alert(1)"/><path d="M0 0h1"/></a></svg>',
    "onload": '<svg xmlns="http://www.w3.org/2000/svg" onload="x()"><path d="M0 0h1"/></svg>',
    "external image": '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://e.com/a.png"/></svg>',
}
for k, s in bad.items():
    check(core.clean_svg(s) is None, f"rejects {k}")
check(core.clean_svg('<svg xmlns="http://www.w3.org/2000/svg"><a href="https://apple.com"><path d="M0 0h1"/></a></svg>') is not None,
      "keeps a normal https link")

# ---- svg_title ----
SI = ('<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
      '<title>ABC</title><path d="M0 0h24v24z"/></svg>')
check(core.svg_title(SI) == "ABC", "svg_title reads <title>")

# ---- Wikidata query + pick ----
q = core.wikidata_query("BRK-B", "berkshirehathaway.com")
check("pq:P249" in q and "wdt:P249" in q and "wdt:P856" in q and q.count("UNION") == 2,
      "query has ticker (both forms) + website in one UNION")
check('"BRK.B"' in q and '"BRK-B"' in q, "query has BRK.B and BRK-B")
check("wdt:P856 ?w" not in core.wikidata_query("AAPL", None), "no website anchor without domain")

rows = [  # ticker 'BA' on two exchanges -> two companies
    {"item": "Q1", "label": "Boeing", "site": "https://www.boeing.com/", "logo": "http://c/Boeing.svg", "rank": "NormalRank"},
    {"item": "Q2", "label": "Bank Albilad", "site": "https://bankalbilad.com", "logo": "http://c/BA2.svg", "rank": "NormalRank"},
]
check(core.pick_wikidata_logo(rows, "boeing.com") == "http://c/Boeing.svg", "picks by website")
check(core.pick_wikidata_logo(rows, None, "The Boeing Company") == "http://c/Boeing.svg", "no website: picks by name")
check(core.pick_wikidata_logo(rows, None, "") is None, "no website, no name, 2 companies: refuses")
check(core.pick_wikidata_logo(rows[:1] + [dict(rows[0], logo="http://c/old.svg", end="2010")], "boeing.com")
      == "http://c/Boeing.svg", "skips retired logo")


# ---- fake network for the rest ----
class R:
    def __init__(self, code=200, text="", ctype="text/html", url=""):
        self.status_code, self.text, self.url = code, text, url
        self.content = text.encode()
        self.headers = {"Content-Type": ctype}

    def json(self):
        import json
        return json.loads(self.text)


calls = []
lock = threading.Lock()


def fake_get(url, headers=None, params=None, timeout=20, tries=3):
    with lock:
        calls.append(url)
    if url == "https://bare.com/":
        raise core.requests.ConnectionError("nope")
    if url == "https://www.bare.com/":
        return R(200, '<link rel="icon" href="/i.svg">', url=url)
    if url.startswith("https://cdn.simpleicons.org/"):
        return R(200, SI, "image/svg+xml")
    if url.endswith("/i.svg"):
        return R(200, OK, "image/svg+xml")
    return R(404, "")


core.http_get = fake_get

# home page: www fallback + cached
core.clear_page_cache()
calls.clear()
check(core.site_svg("bare.com") == OK, "site_svg finds icon via www. fallback")
n1 = calls.count("https://bare.com/") + calls.count("https://www.bare.com/")
core.site_icon("bare.com")
n2 = calls.count("https://bare.com/") + calls.count("https://www.bare.com/")
check(n1 == 2 and n2 == 2, f"home page fetched once per run (requests {n1} -> {n2})")

# Simple Icons guard
logs = []
check(core.simple_icon_svg("abc.xyz", "Alphabet Inc.", "GOOGL", logs.append) is None,
      "Simple Icons 'ABC' refused for Alphabet")
check(logs and "ABC" in logs[0], "...and it says so in the log")
check(core.simple_icon_svg("abc.com", "ABC Holdings", "ABCX") is not None, "Simple Icons kept when name fits")
calls.clear()
core.simple_icon_svg("investor.abc.com")
check(calls == ["https://cdn.simpleicons.org/abc"], f"slug from main domain ({calls})")

# Brand API refund on network error
def boom(*a, **k):
    raise core.requests.ConnectionError("down")


core._SESSION.get = boom
with tempfile.TemporaryDirectory() as td:
    up = os.path.join(td, "u.json")
    usage = {"month": core.month_key(), "used": 5}
    f = core.Fetcher("key", "", "", "icon", 100, usage, up, print)
    f.brand_api_data("AAPL", "apple.com")
    check(usage["used"] == 5, f"Brand API not counted when unreachable (used={usage['used']})")

print("\nFAILED" if fails else "\nALL PASSED", fails)
sys.exit(1 if fails else 0)
