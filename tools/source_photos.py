#!/usr/bin/env python3
"""Source plant photographs from Wikimedia Commons, preferring whole-plant shots.

The plate on the site is the first thing anyone sees of a species, and a
macro of one flower tells you nothing about what the plant will look like in
a front yard. This tool ranks every Commons candidate for each species by how
likely it is to show the plant's *habit* (the whole plant, ideally with some
of its surroundings) and puts that shot first.

Two signals feed the ranking:

  * metadata  -- file title, description and categories. "habit", "shrub",
                 "in habitat", place names push a file up; "flower",
                 "close-up", "macro", "inflorescence", "cone", "bark" push it
                 down.
  * content   -- the picture itself. Close-ups have a sharp subject against a
                 blurred background, so only part of the frame carries edge
                 detail. Habit shots are sharp edge-to-edge and often include
                 sky. (Sky is excluded from the sharpness measure so a shrub
                 against a big blue sky is not mistaken for a macro.)

Subcommands (run from the repository root):

  plan     query Commons for every species (or --ids a,b,c), score the
           candidates, download the top few at 1280px into tools/cache/, and
           write tools/candidates.json plus a contact sheet at
           tools/candidates.html for eyeballing before committing to a pick.
  fetch    take the picks from candidates.json, encode them to WebP in
           web/images/, rewrite tools/photos.json and the PHOTOS constant in
           web/index.html.
  refresh  plan then fetch.
  reorder  offline: within the photos already on disk, move the best
           whole-plant shot into each species' lead slot. Needs no network.
  audit    offline: score what is currently in web/images/ and report which
           species lead with a close-up. Needs no network.

Hand overrides live in tools/overrides.json (synonyms to search under, files
to pin, files to exclude). Pinned files always win; excluded files are never
considered.

Requires Pillow with WebP support (pip install Pillow). Wikimedia asks for a
descriptive User-Agent with contact details and no concurrent bulk downloads,
so everything here is serial and polite.
"""

import argparse
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
WEB = os.path.join(ROOT, "web")
IMAGES = os.path.join(WEB, "images")
INDEX = os.path.join(WEB, "index.html")
CACHE = os.path.join(TOOLS, "cache")
OVERRIDES = os.path.join(TOOLS, "overrides.json")
CANDIDATES = os.path.join(TOOLS, "candidates.json")
CONTACT_SHEET = os.path.join(TOOLS, "candidates.html")
MANIFEST = os.path.join(TOOLS, "photos.json")

API = "https://commons.wikimedia.org/w/api.php"
UA = "MojaveYardReview/2.0 (personal garden planning tool; gmercer015@gmail.com)"

PER_SPECIES = 3          # photos shipped per species
PROBE = 8                # candidates whose pixels we download and inspect
MIN_LONG_EDGE = 900      # reject tiny originals
MAX_EDGE = 560           # encoded size (README recipe)
TARGET_BYTES = 44 * 1024
WEBP_QUALITY = 70

# --- metadata scoring ------------------------------------------------------
# Weight per keyword. Each keyword counts once per file no matter how often it
# appears. Matching is on whole words after underscores/hyphens become spaces.
KEYWORDS = {
    # whole-plant / in-situ
    "habit": 5, "habitus": 5, "form": 3, "whole plant": 5, "whole": 1,
    "shrub": 3, "shrubs": 3, "bush": 2, "tree": 3, "trees": 3, "plant": 1,
    "plants": 2, "habitat": 4, "landscape": 3, "scenery": 2, "vegetation": 2,
    "stand": 2, "colony": 2, "grove": 2, "clump": 2, "tussock": 2, "bunch": 1,
    "desert": 2, "canyon": 2, "valley": 2, "wash": 2, "bajada": 2, "slope": 2,
    "hillside": 2, "ridge": 1, "mesa": 1, "dunes": 2, "flat": 1, "trail": 2,
    "park": 2, "monument": 2, "preserve": 2, "wilderness": 2, "refuge": 2,
    "reserve": 1, "garden": 1, "yard": 2, "roadside": 2, "growing": 1,
    "mojave": 1, "sonoran": 1, "nevada": 1, "arizona": 1, "california": 1,
    "utah": 1, "joshua": 1,
    # close-ups and parts
    "flower": -3, "flowers": -3, "bloom": -3, "blooms": -3, "blooming": -1,
    "blossom": -3, "blossoms": -3, "inflorescence": -4, "inflorescences": -4,
    "raceme": -4, "panicle": -3, "spike": -3, "spikelet": -4, "spikelets": -4,
    "floret": -4, "petal": -4, "petals": -4, "stamen": -4, "stamens": -4,
    "pistil": -4, "anther": -4, "pollen": -4, "bud": -3, "buds": -3,
    "head": -2, "heads": -2, "close": -4, "closeup": -4, "close-up": -4,
    "close up": -4, "macro": -5, "detail": -3, "details": -3, "zoom": -3,
    "leaf": -3, "leaves": -3, "foliage": -2, "needle": -3, "needles": -2,
    "fruit": -3, "fruits": -3, "berry": -3, "berries": -3, "seed": -3,
    "seeds": -3, "seedhead": -2, "pod": -3, "pods": -3, "cone": -3,
    "cones": -3, "capsule": -3, "capsules": -3, "nut": -3, "nuts": -3,
    "spine": -3, "spines": -3, "thorn": -3, "thorns": -3, "glochid": -4,
    "glochids": -4, "areole": -4, "areoles": -4, "bark": -3, "trunk": -2,
    "stem": -2, "stems": -2, "twig": -3, "twigs": -3, "branch": -2,
    "root": -3, "roots": -3, "seedling": -2, "seedlings": -2, "gall": -4,
    "insect": -3, "bee": -3, "butterfly": -3, "bird": -2, "hummingbird": -2,
    "herbarium": -6, "specimen": -3, "illustration": -6, "drawing": -6,
    "botanical": -1, "plate": -3, "map": -8, "distribution": -6, "range": -4,
    "sign": -4, "label": -4, "cultivar": -1, "pot": -2, "potted": -3,
    "nursery": -1, "fungus": -4, "lichen": -4, "mistletoe": -2,
}
# Category names get a stronger, dedicated read: "Flowers of Larrea
# tridentata" is a curated statement that the file is a flower shot.
CATEGORY_HINTS = {
    "habit": 5, "habitus": 5, "plants": 2, "shrubs": 3, "trees": 3,
    "landscapes": 3, "vegetation": 2, "in habitat": 4,
    "flowers": -5, "inflorescences": -5, "fruits": -5, "seeds": -5,
    "leaves": -5, "foliage": -4, "bark": -5, "trunks": -4, "stems": -4,
    "spines": -5, "cones": -5, "buds": -5, "close-ups": -6, "close-up": -6,
    "macro": -6, "details": -4, "herbarium": -8, "illustrations": -8,
    "drawings": -8, "maps": -10, "insects": -4, "unidentified": -4,
    "cultivated": -1, "botanical gardens": 0, "sculptures": -8,
}

OK_LICENCES = re.compile(r"^(cc0|cc[- ]by(-sa)?(\s|$)|public domain|pd\b)", re.I)


def norm_text(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("_", " ").replace("-", " ").replace("/", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def words(s):
    return set(re.findall(r"[a-z0-9]+", s))


def metadata_score(title, description, categories, names):
    """Score from text alone. `names` are strings (scientific and common names,
    synonyms) whose words must not count as keywords -- 'Paper Flower' is a
    species, not a close-up."""
    blob = norm_text(title) + " " + norm_text(description)
    for n in names:
        blob = blob.replace(norm_text(n), " ")
    w = words(blob)
    score = 0.0
    hits = []
    for kw, wt in KEYWORDS.items():
        parts = kw.split(" ")
        if (len(parts) == 1 and kw in w) or (len(parts) > 1 and kw in blob):
            score += wt
            hits.append(kw)
    for cat in categories:
        c = norm_text(cat)
        for n in names:
            c = c.replace(norm_text(n), " ")
        for kw, wt in CATEGORY_HINTS.items():
            if kw in c and wt:
                score += wt
                hits.append("cat:" + kw)
    return score, hits


def shape_score(w, h):
    if not w or not h:
        return 0.0
    r = w / h
    if r >= 1.15:
        return 1.0            # landscape fits the 4:3 plate without cropping
    if r <= 0.67:
        return -1.5           # tall portrait gets cropped hard and is usually a stalk
    if r < 1.0:
        return -0.5
    return 0.0


# --- content scoring -------------------------------------------------------

def content_features(path_or_bytes):
    """Return (spread, sky, saturation) for an image.

    spread     fraction of non-sky tiles whose edge energy is at least a third
               of the sharpest tile. ~1.0 means sharp everywhere (habit shot);
               below ~0.5 means one sharp subject on a soft background (macro).
    sky        fraction of the top third that is bright and blue.
    saturation mean HSV saturation, 0..1. Flower macros run high.
    """
    from PIL import Image, ImageFilter, ImageOps, ImageStat

    im = Image.open(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes)
    im = im.convert("RGB")
    im.thumbnail((320, 320))
    W, H = im.size
    edges = ImageOps.grayscale(im).filter(ImageFilter.FIND_EDGES)
    n = 8
    energy, skyish = [], []
    for j in range(n):
        for i in range(n):
            box = (i * W // n, j * H // n, (i + 1) * W // n, (j + 1) * H // n)
            energy.append(ImageStat.Stat(edges.crop(box)).mean[0])
            r, g, b = ImageStat.Stat(im.crop(box)).mean
            skyish.append(b > 140 and b >= r + 12 and b >= g and j < n // 2)
    peak = max(energy) or 1.0
    live = [e for e, s in zip(energy, skyish) if not s]
    if len(live) < n:          # nearly all sky: treat the whole frame as live
        live = energy
    spread = sum(1 for e in live if e > 0.35 * peak) / len(live)
    top = im.crop((0, 0, W, H // 3))
    px = list(top.get_flattened_data() if hasattr(top, "get_flattened_data") else top.getdata())
    sky = sum(1 for r, g, b in px if b > 150 and b >= r + 15 and b >= g) / max(1, len(px))
    sat = ImageStat.Stat(im.convert("HSV").getchannel("S")).mean[0] / 255.0
    return spread, sky, sat


def content_score(spread, sky, sat):
    s = (spread - 0.55) * 10          # -5.5 .. +4.5
    s += min(sky, 0.5) * 3            # up to +1.5 for a horizon
    if sat > 0.6:
        s -= (sat - 0.6) * 8          # saturated frames are usually flowers
    return round(s, 2)


def classify(meta_hits, spread):
    closeup_words = {k for k, v in KEYWORDS.items() if v <= -3} | {"cat:" + k for k, v in CATEGORY_HINTS.items() if v <= -4}
    if any(h in closeup_words for h in meta_hits):
        return "close-up"      # the uploader told us what it is; believe them
    if spread is not None and spread < 0.45:
        return "close-up"
    if spread is not None and spread >= 0.6:
        return "habit"
    return "unclear"


# --- species and overrides -------------------------------------------------

def load_species(ids=None):
    src = open(INDEX, encoding="utf-8").read()
    out = []
    for m in re.finditer(r'\{id:"(\w+)",n:"([^"]+)",b:"([^"]+)",s:"(\w+)".*?q:"([^"]+)"', src):
        pid, common, sci, _stratum, q = m.groups()
        if ids and pid not in ids:
            continue
        out.append({"id": pid, "common": common, "sci": sci, "q": q})
    if not out:
        sys.exit("no species matched; check web/index.html and --ids")
    return out


def load_overrides():
    if not os.path.exists(OVERRIDES):
        return {"synonyms": {}, "pin": {}, "exclude": {}}
    o = json.load(open(OVERRIDES, encoding="utf-8"))
    for k in ("synonyms", "pin", "exclude"):
        o.setdefault(k, {})
    return o


def canon_title(t):
    t = html.unescape(t or "").strip()
    if not t.lower().startswith("file:"):
        t = "File:" + t
    return "File:" + t[5:].replace("_", " ")


# --- Commons API -----------------------------------------------------------

class Commons:
    def __init__(self, pause=0.4):
        self.pause = pause
        self.last = 0.0

    def _get(self, url, binary=False, timeout=120):
        delay = 1.6
        for attempt in range(6):
            wait = self.pause - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = r.read()
                self.last = time.time()
                return data if binary else json.loads(data.decode("utf-8"))
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
                code = getattr(e, "code", None)
                if attempt == 5:
                    raise
                time.sleep(delay * (3 if code == 429 else 1))
                delay *= 1.8

    def api(self, **params):
        params.update(action=params.get("action", "query"), format="json", formatversion=2, maxlag=5)
        return self._get(API + "?" + urllib.parse.urlencode(params))

    def category_files(self, cat, depth=1):
        """Files directly in Category:<cat> plus files in its immediate
        subcategories, tagged with the subcategory name they came from."""
        found = {}
        cont = {}
        while True:
            r = self.api(list="categorymembers", cmtitle="Category:" + cat, cmtype="file|subcat",
                         cmlimit=500, **cont)
            for m in r.get("query", {}).get("categorymembers", []):
                if m["ns"] == 6:
                    found.setdefault(canon_title(m["title"]), set())
                elif m["ns"] == 14 and depth > 0:
                    sub = m["title"][len("Category:"):]
                    for f, tags in self.category_files(sub, depth - 1).items():
                        found.setdefault(f, set()).update(tags | {sub})
            if "continue" in r:
                cont = r["continue"]
            else:
                return found

    def search_files(self, term, limit=60):
        r = self.api(list="search", srnamespace=6, srlimit=limit,
                     srsearch='"%s" filetype:bitmap' % term)
        return [canon_title(h["title"]) for h in r.get("query", {}).get("search", [])]

    def imageinfo(self, titles, width=1280):
        out = {}
        for i in range(0, len(titles), 50):
            batch = titles[i:i + 50]
            r = self.api(prop="imageinfo", titles="|".join(batch),
                         iiprop="url|size|mime|extmetadata", iiurlwidth=width,
                         iiextmetadatafilter="Artist|LicenseShortName|ImageDescription|Categories|Credit|Attribution")
            for page in r.get("query", {}).get("pages", []):
                if "imageinfo" not in page or page.get("missing"):
                    continue
                ii = page["imageinfo"][0]
                em = ii.get("extmetadata", {})
                val = lambda k: em.get(k, {}).get("value", "")
                out[canon_title(page["title"])] = {
                    "title": canon_title(page["title"]),
                    "page": ii.get("descriptionurl") or "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(page["title"].replace(" ", "_")),
                    "url": ii.get("url"),
                    "thumb": ii.get("thumburl") or ii.get("url"),
                    "width": ii.get("width", 0), "height": ii.get("height", 0),
                    "mime": ii.get("mime", ""),
                    "artist": norm_artist(val("Artist") or val("Credit") or val("Attribution")),
                    "licence": html.unescape(re.sub(r"<[^>]+>", "", val("LicenseShortName"))).strip(),
                    "description": html.unescape(re.sub(r"<[^>]+>", " ", val("ImageDescription"))).strip(),
                    "categories": [c for c in val("Categories").split("|") if c],
                }
        return out

    def download(self, url):
        time.sleep(1.5)     # bulk-download etiquette
        return self._get(url, binary=True)


def norm_artist(s):
    s = html.unescape(re.sub(r"<[^>]+>", "", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^(photo(graph)?\s+by|by)\s+", "", s, flags=re.I)
    return s[:80] or "Wikimedia Commons"


def credit(info):
    return "%s · %s" % (info["artist"], info["licence"] or "see source")


# --- plan --------------------------------------------------------------------

def plan(args):
    species = load_species(args.ids)
    ov = load_overrides()
    c = Commons()
    os.makedirs(CACHE, exist_ok=True)
    result = json.load(open(CANDIDATES)) if os.path.exists(CANDIDATES) and args.ids else {}

    for sp in species:
        pid = sp["id"]
        names = [sp["sci"], sp["q"], sp["common"]] + ov["synonyms"].get(pid, [])
        pins = [canon_title(t) for t in ov["pin"].get(pid, [])]
        excl = {canon_title(t) for t in ov["exclude"].get(pid, [])}
        print("== %s  %s (%s)" % (pid, sp["sci"], sp["common"]), flush=True)

        tags = {}
        for name in dict.fromkeys([sp["sci"], sp["q"]] + ov["synonyms"].get(pid, [])):
            try:
                for f, t in c.category_files(name).items():
                    tags.setdefault(f, set()).update(t)
            except Exception as e:      # category may not exist
                print("   category %r: %s" % (name, str(e)[:80]))
            for f in c.search_files(name):
                tags.setdefault(f, set())
        for p in pins:
            tags.setdefault(p, set())
        titles = [t for t in tags if t not in excl][:args.max_candidates]
        print("   %d candidates" % len(titles), flush=True)

        infos = c.imageinfo(titles)
        scored = []
        for t, info in infos.items():
            if not info["mime"].startswith("image/") or info["mime"] in ("image/svg+xml", "image/gif"):
                continue
            if max(info["width"], info["height"]) < MIN_LONG_EDGE:
                continue
            if not OK_LICENCES.search(info["licence"] or "") and t not in pins:
                continue
            ms, hits = metadata_score(t, info["description"], info["categories"] + sorted(tags.get(t, ())), names)
            ss = shape_score(info["width"], info["height"])
            info.update(meta=ms, hits=hits, shape=ss, pinned=t in pins, content=None, features=None)
            info["score"] = ms + ss + (100 if t in pins else 0)
            scored.append(info)
        scored.sort(key=lambda x: -x["score"])

        # Download pixels for the top few and let the picture itself vote.
        for info in scored[:args.probe]:
            fn = os.path.join(CACHE, cache_name(info["title"]))
            try:
                if not (os.path.exists(fn) and os.path.getsize(fn) > 2000):
                    open(fn, "wb").write(c.download(info["thumb"]))
                spread, sky, sat = content_features(fn)
                info["features"] = {"spread": round(spread, 2), "sky": round(sky, 2), "sat": round(sat, 2)}
                info["content"] = content_score(spread, sky, sat)
                info["score"] = info["meta"] + info["shape"] + info["content"] + (100 if info["pinned"] else 0)
                info["kind"] = classify(info["hits"], spread)
                info["cache"] = os.path.relpath(fn, TOOLS)
            except Exception as e:
                print("   download failed %s: %s" % (info["title"], str(e)[:80]))
                info["score"] -= 50
        scored.sort(key=lambda x: -x["score"])
        picks = choose(scored, args.per_species)
        for info in scored[:args.probe]:
            mark = "*" if info in picks else " "
            print("   %s %6.1f  %-8s %s" % (mark, info["score"], info.get("kind", "?"), info["title"][5:][:70]))
        result[pid] = {"species": sp, "picks": [i["title"] for i in picks],
                       "candidates": [strip_info(i) for i in scored[:max(args.probe, 12)]]}
        json.dump(result, open(CANDIDATES, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    write_contact_sheet(result)
    print("wrote", os.path.relpath(CANDIDATES, ROOT), "and", os.path.relpath(CONTACT_SHEET, ROOT))


def choose(scored, n):
    """Lead with the best habit shot; never lead with a close-up. Fill the rest
    by score, one file per photographer where possible so three shots of the
    same plant from the same afternoon do not crowd out a second viewpoint."""
    probed = [i for i in scored if i.get("cache")]
    lead = next((i for i in probed if i.get("kind") == "habit"), None) \
        or next((i for i in probed if i.get("kind") == "unclear"), None)
    picks = [lead] if lead else []
    seen_artists = {lead["artist"]} if lead else set()
    for i in probed:
        if len(picks) >= n:
            break
        if i in picks:
            continue
        if i["artist"] in seen_artists and len(probed) > n:
            continue
        picks.append(i)
        seen_artists.add(i["artist"])
    for i in probed:            # top up if the artist rule left slots empty
        if len(picks) >= n:
            break
        if i not in picks:
            picks.append(i)
    return picks


def strip_info(i):
    keep = ("title", "page", "url", "thumb", "width", "height", "artist", "licence", "description",
            "categories", "meta", "hits", "shape", "content", "features", "score", "kind", "pinned", "cache")
    return {k: i.get(k) for k in keep}


def cache_name(title):
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", title[5:])[:120]
    return base


def write_contact_sheet(result):
    rows = []
    for pid, r in result.items():
        sp = r["species"]
        cells = []
        for cnd in r["candidates"]:
            if not cnd.get("cache"):
                continue
            pick = cnd["title"] in r["picks"]
            f = cnd.get("features") or {}
            cells.append(
                '<figure class="%s"><img src="%s" loading="lazy"><figcaption>'
                '<b>%.1f</b> %s · spread %s · sky %s<br><a href="%s" target="_blank">%s</a><br>%s</figcaption></figure>'
                % ("pick" if pick else "", html.escape(cnd["cache"]), cnd["score"], cnd.get("kind", "?"),
                   f.get("spread", "?"), f.get("sky", "?"), html.escape(cnd["page"]),
                   html.escape(cnd["title"][5:][:60]), html.escape(", ".join(cnd["hits"])[:120])))
        rows.append("<section><h2>%s <small>%s</small></h2><div class=row>%s</div></section>"
                    % (html.escape(sp["sci"]), html.escape(sp["common"]), "".join(cells)))
    open(CONTACT_SHEET, "w", encoding="utf-8").write(
        "<!doctype html><meta charset=utf-8><title>Photo candidates</title><style>"
        "body{font:13px system-ui;margin:20px;background:#111;color:#ddd}.row{display:flex;gap:10px;overflow-x:auto}"
        "figure{margin:0;width:260px;flex:none;border:3px solid #333;padding:4px}figure.pick{border-color:#7ecf6a}"
        "img{width:100%;aspect-ratio:4/3;object-fit:cover}figcaption{font-size:11px;line-height:1.35}"
        "a{color:#9cf}small{color:#999;font-weight:normal}</style>"
        "<h1>Photo candidates</h1><p>Green border = current pick. Edit tools/overrides.json (pin / exclude) and re-run "
        "<code>plan --ids &lt;id&gt;</code> to change a species, then <code>fetch</code>.</p>" + "".join(rows))


# --- fetch -------------------------------------------------------------------

def fetch(args):
    from PIL import Image

    if not os.path.exists(CANDIDATES):
        sys.exit("run `plan` first; tools/candidates.json is missing")
    cands = json.load(open(CANDIDATES, encoding="utf-8"))
    manifest = load_manifest()
    c = Commons()
    ids = args.ids or list(cands)
    for pid in ids:
        r = cands.get(pid)
        if not r:
            print("no candidates for", pid)
            continue
        by_title = {x["title"]: x for x in r["candidates"]}
        entries = []
        for i, title in enumerate(r["picks"]):
            info = by_title[title]
            fn = os.path.join(CACHE, cache_name(title))
            if not os.path.exists(fn):
                open(fn, "wb").write(c.download(info["thumb"]))
            out = os.path.join(IMAGES, "%s_%d.webp" % (pid, i))
            size = encode(Image.open(fn), out)
            entries.append({
                "file": "/images/%s_%d.webp" % (pid, i), "title": title, "page": info["page"],
                "artist": info["artist"], "licence": info["licence"], "credit": credit(info),
                "kind": info.get("kind"), "score": info["score"], "bytes": size,
            })
            print("   %s  %5dKB  %s" % (os.path.basename(out), size // 1024, title[5:][:60]))
        for stale in os.listdir(IMAGES):
            if stale.startswith(pid + "_") and int(stale.split("_")[1].split(".")[0]) >= len(entries):
                os.remove(os.path.join(IMAGES, stale))
        manifest[pid] = entries
    save_manifest(manifest)
    patch_index(manifest)
    print("updated web/images, tools/photos.json and web/index.html")


def encode(im, out):
    """Resize to the plate's working size and step the dimension down until
    the WebP fits the budget. Portrait shots are sized by width so the crop
    the plate shows stays sharp."""
    im = im.convert("RGB")
    w, h = im.size
    portrait = h > w
    edge = MAX_EDGE
    while True:
        scale = edge / (w if portrait else max(w, h))
        if scale > 1:
            scale = 1.0
        rs = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), resample=3)
        buf = io.BytesIO()
        rs.save(buf, "WEBP", quality=WEBP_QUALITY, method=6)
        if buf.tell() <= TARGET_BYTES or edge <= 320:
            open(out, "wb").write(buf.getvalue())
            return buf.tell()
        edge -= 40


def load_manifest():
    if os.path.exists(MANIFEST):
        return json.load(open(MANIFEST, encoding="utf-8"))
    # Bootstrap from whatever the page currently ships.
    src = open(INDEX, encoding="utf-8").read()
    m = re.search(r"^const PHOTOS=(\{.*\});$", src, re.M)
    photos = json.loads(m.group(1)) if m else {}
    out = {}
    for pid, arr in photos.items():
        out[pid] = [{"file": e["t"], "title": canon_title(urllib.parse.unquote(e["p"].rsplit("/", 1)[-1])),
                     "page": e["p"], "credit": e["c"]} for e in arr]
    return out


def save_manifest(manifest):
    json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    open(MANIFEST, "a").write("\n")


def patch_index(manifest):
    src = open(INDEX, encoding="utf-8").read()
    compact = {pid: [{"t": e["file"], "p": e["page"], "c": e["credit"]} for e in arr]
               for pid, arr in manifest.items() if arr}
    line = "const PHOTOS=" + json.dumps(compact, separators=(",", ":"), ensure_ascii=True) + ";"
    new, n = re.subn(r"^const PHOTOS=\{.*\};$", lambda _: line, src, count=1, flags=re.M)
    if n != 1:
        sys.exit("could not find `const PHOTOS=` in web/index.html")
    open(INDEX, "w", encoding="utf-8").write(new)


# --- audit -------------------------------------------------------------------

def audit(args):
    species = load_species(args.ids)
    manifest = load_manifest()
    ov = load_overrides()
    leads_bad = []
    print("%-5s %-28s %-9s %6s %6s  %s" % ("id", "species", "kind", "spread", "meta", "lead photo"))
    for sp in species:
        pid = sp["id"]
        arr = manifest.get(pid) or []
        names = [sp["sci"], sp["q"], sp["common"]] + ov["synonyms"].get(pid, [])
        first = True
        for e in arr:
            path = os.path.join(WEB, e["file"].lstrip("/"))
            if not os.path.exists(path):
                continue
            spread, sky, sat = content_features(path)
            ms, hits = metadata_score(e.get("title", ""), "", [], names)
            kind = classify(hits, spread)
            e["kind"] = kind
            if args.all or first:
                print("%-5s %-28s %-9s %6.2f %6.1f  %s" % (pid if first else "", sp["sci"][:28] if first else "",
                                                         kind, spread, ms, e.get("title", "")[5:][:60]))
            if first and kind != "habit":
                leads_bad.append(pid)
            first = False
    print("\n%d of %d species lead with a photo that is not a clear whole-plant shot: %s"
          % (len(leads_bad), len(species), " ".join(leads_bad)))


# --- reorder -----------------------------------------------------------------

def reorder(args):
    """Offline: within what is already on disk, move the best whole-plant shot
    into the lead slot for each species. Files are renamed so the lead is
    always <id>_0.webp, the manifest and the page follow."""
    species = load_species(args.ids)
    manifest = load_manifest()
    ov = load_overrides()
    changed = 0
    for sp in species:
        pid = sp["id"]
        arr = [e for e in manifest.get(pid) or [] if os.path.exists(os.path.join(WEB, e["file"].lstrip("/")))]
        if len(arr) < 2:
            continue
        names = [sp["sci"], sp["q"], sp["common"]] + ov["synonyms"].get(pid, [])
        excl = {canon_title(t) for t in ov["exclude"].get(pid, [])}
        pins = [canon_title(t) for t in ov["pin"].get(pid, [])]
        for e in arr:
            spread, sky, sat = content_features(os.path.join(WEB, e["file"].lstrip("/")))
            ms, hits = metadata_score(e.get("title", ""), "", [], names)
            e["kind"] = classify(hits, spread)
            e["score"] = round(ms + content_score(spread, sky, sat), 2)
            if canon_title(e.get("title", "")) in pins:
                e["score"] += 100
            if canon_title(e.get("title", "")) in excl:
                e["score"] -= 100
        rank = {"habit": 0, "unclear": 1, "close-up": 2}
        order = sorted(arr, key=lambda e: (rank[e["kind"]], -e["score"]))
        if [e["file"] for e in order] == [e["file"] for e in arr]:
            continue
        # two-phase rename so a swap never clobbers a file
        tmp = []
        for i, e in enumerate(order):
            src = os.path.join(WEB, e["file"].lstrip("/"))
            t = os.path.join(IMAGES, ".%s_%d.tmp" % (pid, i))
            os.rename(src, t)
            tmp.append(t)
        for i, (e, t) in enumerate(zip(order, tmp)):
            e["file"] = "/images/%s_%d.webp" % (pid, i)
            os.rename(t, os.path.join(WEB, e["file"].lstrip("/")))
        manifest[pid] = order
        changed += 1
        print("%-5s now leads with %-9s %s" % (pid, order[0]["kind"], order[0].get("title", "")[5:][:60]))
    save_manifest(manifest)
    patch_index(manifest)
    print("%d species reordered" % changed)


# --- main --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--ids", type=lambda s: [x for x in s.split(",") if x], help="comma-separated species ids")

    p = sub.add_parser("plan"); common(p)
    p.add_argument("--probe", type=int, default=PROBE, help="candidates to download and inspect per species")
    p.add_argument("--per-species", type=int, default=PER_SPECIES)
    p.add_argument("--max-candidates", type=int, default=150)
    p = sub.add_parser("fetch"); common(p)
    p = sub.add_parser("refresh"); common(p)
    p.add_argument("--probe", type=int, default=PROBE)
    p.add_argument("--per-species", type=int, default=PER_SPECIES)
    p.add_argument("--max-candidates", type=int, default=150)
    p = sub.add_parser("reorder"); common(p)
    p = sub.add_parser("audit"); common(p)
    p.add_argument("--all", action="store_true", help="list every photo, not just each species' lead")

    args = ap.parse_args()
    if args.cmd == "plan":
        plan(args)
    elif args.cmd == "fetch":
        fetch(args)
    elif args.cmd == "refresh":
        plan(args)
        fetch(args)
    elif args.cmd == "reorder":
        reorder(args)
    elif args.cmd == "audit":
        audit(args)


if __name__ == "__main__":
    main()
