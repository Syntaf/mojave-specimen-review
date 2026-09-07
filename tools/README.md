# tools

Provenance and sourcing for the images in `web/images/`.

## Files

| File | What |
|---|---|
| `photos.json` | The authoritative manifest: for every shipped image, the Commons file title and page, photographer, licence, and the classification the tool gave it. Committed. |
| `overrides.json` | Hand rules: extra names to search under, files to **pin** (always chosen, always first) and files to **exclude**. Committed. |
| `source_photos.py` | The sourcing pipeline. |
| `test_source_photos.py` | Offline tests for the scoring and the manifest/page rewrite. |
| `cache/`, `candidates.json`, `candidates.html` | Working files from the last `plan` run. Ignored by git. |

## Why the pipeline exists

The plate is the first thing you see of a species, and a macro of one flower
says nothing about what the plant does in a front yard. The first pass at
sourcing took the top Commons search hits, which skew heavily towards flower and
fruit close-ups. `source_photos.py` ranks candidates by how likely they are to
show the plant's **habit** — the whole plant, ideally with some ground and sky
around it — and puts that shot first. Close-ups are still welcome as the second
or third photo; they are just never the lead.

Two signals feed the ranking:

- **Metadata.** File title, description and categories. `habit`, `shrub`,
  `in habitat`, place names and park names push a file up. `flower`,
  `inflorescence`, `close-up`, `macro`, `cone`, `bark`, `leaf` push it down, and
  a curated Commons category such as *Flowers of Larrea tridentata* is treated
  as the uploader's word for it. Words that are part of the species' own name
  (*Paper Flower*, *Single-leaf pinyon*) are stripped before matching.
- **Content.** Close-ups have one sharp subject against a soft background, so
  only part of the frame carries edge detail; habit shots are sharp edge to
  edge and usually include sky. The tool measures the fraction of the frame
  that is sharp (sky excluded, so a bush against a big blue sky is not mistaken
  for a macro), the amount of sky, and colour saturation.

The content signal reliably catches shallow-depth-of-field macros. It cannot
tell a frame-filling tangle of leaves from a frame-filling shrub, which is what
`overrides.json` is for: the contact sheet makes those obvious in a few seconds.

The search is also wider than before: each species is looked up under its
Commons category (one level of subcategories included, and the subcategory name
becomes a metadata signal), under a full-text search, and under every synonym
in `overrides.json` — old binomials like *Acacia greggii* and *Oryzopsis
hymenoides* hold a lot of the good in-situ photographs.

## Running it

Needs Python 3.9+ and Pillow with WebP support (`pip install Pillow`), and
network access to `commons.wikimedia.org` and `upload.wikimedia.org`. Run from
the repository root.

```sh
python3 tools/source_photos.py plan            # query, score, download top candidates, write contact sheet
open tools/candidates.html                     # eyeball the picks; pin/exclude in overrides.json as needed
python3 tools/source_photos.py plan --ids latr,chli   # re-plan just the species you adjusted
python3 tools/source_photos.py fetch           # encode picks into web/images, rewrite photos.json and index.html
```

`make photos` runs `plan` then `fetch` in one go.

Offline helpers that need no network:

```sh
python3 tools/source_photos.py audit --all     # classify what ships today; lists species that lead with a close-up
python3 tools/source_photos.py reorder         # move the best whole-plant shot already on disk into the lead slot
python3 -m unittest tools/test_source_photos.py
```

Wikimedia rate-limits concurrent bulk downloads (HTTP 429), so everything is
serial with a descriptive User-Agent, a pause between API calls and a longer
one between downloads. A full `plan` takes on the order of ten minutes.
Candidates are fetched at 1280px through Commons' pre-rendered thumbnail path
rather than as multi-megabyte originals.

## Encoding

Images are resized to a maximum edge of 560px (portrait shots are sized by width
so the crop the plate shows stays sharp) and re-encoded to WebP at quality 70,
stepping the dimension down per image to hold each file under ~44KB. Files are
named `<species id>_<n>.webp`; `_0` is the lead.
