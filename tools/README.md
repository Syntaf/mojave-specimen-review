# tools

Provenance for the images in `web/images/`.

`../data/photos.json` is the authoritative manifest: for every image it records
the Wikimedia Commons file page, the photographer, and the licence. Images were
fetched from Commons originals (thumbnail URLs are bot-blocked — only whitelisted
widths are served), then resized to a maximum edge of 560px and re-encoded to
WebP at quality 70, with the dimension stepped down per image to hold each file
under ~44KB. Portrait shots are sized by width instead so they stay sharp in the
plate.

`fetch_commons_photos.py` is the polite serial downloader used for that pass —
Wikimedia rate-limits concurrent bulk downloads with HTTP 429.
