# Tamadoo Odoo Apps site

Static marketing site for the Tamadoo modules on the Odoo Apps Store. Every page is generated
from the `tamadoo_store` repository and written to `docs/`, which GitHub Pages serves as is.

## Rebuild

```bash
python3 generator/build.py --site-url https://tamadoo-odoo.github.io/
python3 generator/check_site.py
```

- `--site-url` is the public base URL (default `https://tamadoo-odoo.github.io/`). It is used only for
  canonical URLs, Open Graph images, JSON-LD, `sitemap.xml`, `robots.txt` and `404.html`; all other
  links are relative, so a sub-path such as `https://user.github.io/tamadoo/` works too.
- `--store PATH` points at the `tamadoo_store` repository (default `../tamadoo_store`). Branches
  `20.0` .. `16.0` are read with `git show`; nothing is checked out. Text, banner and screenshots come
  from `20.0`, or the newest series that has the module.
- `--status PATH` is the store audit result (default `../tasks/store_audit/store_status.json`). A series
  that is not live on apps.odoo.com gets no store button, only a "not listed yet" note. `--no-status`
  links every series.
- Run it with a Python that has Pillow (e.g. a workspace Odoo venv) to convert screenshots to WebP and
  banners to JPEG. Plain `python3` without Pillow copies the original PNG files (larger site).
  Converted images are cached in `generator/.cache/` (not committed).
- YouTube videos: add `"tamadoo_<module>": "<youtube id>"` to `generator/videos.json` (or a
  `youtube_id` key in the manifest) and rebuild.
- Own demo videos: put `<module>.mp4` (H.264, faststart) and a `<module>.jpg` poster in `generator/videos/`
  and add `"tamadoo_<module>": {"file": "<module>.mp4", "poster": "<module>.jpg", "seconds": 42.7,
  "date": "YYYY-MM-DD"}` to `generator/videos.json`. The build copies `generator/videos/` to `docs/videos/`
  and shows a `<video>` player near the top of the module page.
- Featured apps and category mapping are at the top of `generator/build.py`; styles are in
  `generator/site.css` (copied to `docs/assets/site.css`).

`check_site.py` checks every internal link and image, tag balance, alt text, unique titles and
descriptions, words listed one per line in the untracked file `generator/.banned_words`, and prints the page count and total size. It exits non-zero on any problem.

## Publish on GitHub Pages

1. Create a public repository (for the root domain: `tamadoo-odoo.github.io` under the
   `tamadoo-odoo` organization) and push this repository's `main` branch to it.
2. Settings > Pages > Build and deployment: Source "Deploy from a branch", branch `main`, folder `/docs`.
3. After the first deploy, submit `<site-url>/sitemap.xml` in Google Search Console.

After any listing change in `tamadoo_store` (names, summaries, pictures, new series), run the two
commands above, commit `docs/` and push.
