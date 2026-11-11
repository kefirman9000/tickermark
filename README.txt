STOCK LOGO DOWNLOADER v6.1 - SIMPLE WINDOWS VERSION (no tkinter needed)

1. Unzip this folder anywhere (not inside the zip).
2. Double-click Install.bat  (one time: sets up Python + packages, makes a desktop shortcut)
3. A page opens in your browser. Choose "Market lists" or "My tickers", press Start,
   and watch the logos appear as they download.
   Logos go to Documents\StockLogos (change it on the page). The gear icon opens
   Settings; changes save automatically (also while a download is running).
   Light/dark follows Windows; the sun button overrides it.
4. Next time: use the desktop shortcut or Run.bat. Press Quit on the page to close.
   Updating: just unzip the new version over the old folder. Your settings and
   logos stay.

NEW IN v6.1
  * More SVGs from Wikidata: it now also finds companies by the ticker on their
    stock-exchange listing (how Wikidata usually stores it), in one query
    instead of two, so it is faster too.
  * Fewer wrong logos: a Simple Icons glyph is only used if its brand name fits
    the company (no more "ABC" for Alphabet), and without a known website a
    Wikidata logo must match the company name.
  * When Yahoo lists an investor-relations site (investor.company.com), the
    main site (company.com) is used, so you get the company's icon, not the
    IR vendor's.
  * Each website's home page is downloaded once per ticker instead of twice,
    and www.<site> is tried when the bare address doesn't answer.
  * SVG safety: links hidden as javascript: inside a logo are now refused.
  * The Brandfetch API counter no longer counts lookups that never reached
    Brandfetch (network down).
  * tests\test_offline.py checks all of this without internet:
    .venv\Scripts\python.exe tests\test_offline.py

NEW IN v6
  * Faster: several tickers are looked up at the same time (Settings > Speed,
    default 4). Requests to each website are still spaced out politely.
  * Progress bar moves for every ticker checked, not only for new logos.
  * More lists: Day gainers, Day losers, S&P 500, Nasdaq-100, Dow 30 (0 = skip),
    plus "Include ETFs".
  * My tickers: "Import CSV / watchlist" reads a file from your broker or a plain
    list. Type TICKER=website (e.g. BRK-B=berkshirehathaway.com) to tell it the
    company's site when Yahoo has none or has the wrong one. It is remembered.
  * Library tab: every logo you have, with search and filters (has SVG, PNG only,
    no logo, my own files, ...). Click any logo (here or in "This run") to:
      - Wrong logo - try another source: the logo is put aside in "Rejected logos"
        and the provider that gave it is skipped for that ticker from now on.
      - Set the company website, then Download again.
      - Use my own file (PNG/SVG/JPG/WebP). It is locked: runs never replace it.
      - Show in folder, Remove (moved to "Removed logos"), or skip the ticker
        in market lists.
    These choices live in overrides.json inside your logo folder.
  * Black & white versions keep inner details: white parts inside a logo become
    see-through instead of filled in (an all-white logo stays a solid shape).
    Black/white SVGs are saved too (SVG logos\Black logos, SVG logos\White logos).
  * PNG finishing (Settings): trim empty edges, square canvas, padding,
    background colour, extra sizes (e.g. 256, 512 -> 256px\ and 512px\ folders,
    never upscaled), and a WebP copy.

OUTPUT
  Documents\StockLogos\SVG logos\   <- the original SVGs (scale to any size)
  Documents\StockLogos\             <- big transparent PNGs rendered from the SVGs
  (Black logos / White logos sub-folders if you pick those colors)
  Where no SVG exists, you get the best real PNG instead (never upscaled).

SVG sources, in order: Brandfetch (SVG), Wikidata/Wikimedia Commons (matched by
ticker, then by website), Simple Icons, the company website's own SVG icon.

SVG QUALITY
  Every SVG is checked before it is saved: it must be valid XML and safe (no
  scripts, event handlers, or links to outside files). The first SVG made of real
  vector shapes wins. One that relies on installed fonts (<text>) or mixes in a
  bitmap is only used if nothing better exists, and the log says so. A bitmap
  wrapped in an SVG tag is never used. Wikidata logos marked as retired are
  skipped in favour of the current one. Downloads retry automatically on timeouts
  and rate limits.

SVGS FOR LOGOS YOU ALREADY HAVE
  With "Skip logos I already have" ticked, the Settings option "Add SVGs to logos
  I already have" adds the missing SVGs without touching your PNGs.
  Logos with no SVG anywhere are remembered (SVG logos\svg_index.json) and not
  searched again for 14 days; a lookup that failed because the network was down is
  retried the next day.

To also re-render the PNGs from SVGs you already have: untick "Skip logos I already
have" once.
If a Yahoo list fails, a Nasdaq backup list is used. Typing tickers into "My
tickers" always works.
