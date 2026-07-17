# AGENTS.md

## What this is

JavSP: Python 3.8 AV metadata scraper. Extracts video IDs from filenames, scrapes metadata from multiple web sources in parallel threads, and outputs NFO files + cover images for Emby/Jellyfin/Kodi.

## Quick start

```bash
pip install -r requirements.txt      # Windows (includes pywin32)
pip install -r requirements-linux.txt # Linux (includes retina-face, keras)
python JavSP.py
```

## Tests

```bash
pip install pytest
pytest unittest/test_avid.py     # ID parsing
pytest unittest/test_file.py     # file handling
pytest unittest/test_func.py     # business logic
pytest unittest/test_crawlers.py # web crawlers (hits live sites)
pytest unittest/test_crawlers.py --only javdb  # test one crawler
```

CI runs `flake8` for linting (syntax errors + undefined names only). There is no formatter or type checker configured.

## Build (PyInstaller)

```bash
# Windows
make.bat

# Linux
make.bash
```

Both generate version hooks, run PyInstaller, then clean up. Output goes to `dist/`.

## Architecture

```
JavSP.py          Entry point. Orchestrates: scan → identify → parallel crawl → merge → NFO/write.
core/
  config.py       Config loading/validation from core/config.ini. Auto-generates defaults on first run.
  datatype.py     Movie (file container) and MovieInfo (scraped metadata) classes.
  avid.py         ID extraction from filenames. Handles special formats: FC2, heydouga, CID, etc.
  func.py         Business logic (folder selection, update checks, title processing).
  file.py         File scanning and movie recognition.
  image.py        Cover/poster processing and cropping.
  nfo.py          NFO XML generation.
  lib.py          Pure utility functions (no custom type dependencies).
web/
  base.py         Request class (wraps requests/cloudscraper), HTML parsing helpers, download.
  exceptions.py   CrawlerError hierarchy (MovieNotFoundError, SiteBlocked, etc.)
  <site>.py       One module per crawler site. Each exports parse_data(info: MovieInfo).
data/             Genre CSV maps and actress alias JSON.
unittest/data/    Test fixtures: JSON files named "{avid} ({scraper}).json".
```

## Crawler conventions

- Each crawler in `web/` defines `parse_data(info: MovieInfo)` that mutates `info` in place.
- Crawlers with custom retry logic define `parse_data_raw` instead (retries set to 1).
- Crawler order is config-driven via `[CrawlerSelect]` sections in `core/config.ini`.
- Three data source types determine which crawler list is used: `normal`, `fc2`, `cid`.
- Use `Request(use_scraper=True)` from `web.base` when Cloudflare protection is expected.
- Crawlers should raise `MovieNotFoundError` (not found) or `MovieDuplicateError` (ambiguous match), not generic exceptions.

## Key gotchas

- **Python 3.8 required.** CI and code both enforce `>= 3.8`. Do not use walrus operator `:=` in non-local scope or f-string `=` debugging.
- **UTF-8 everywhere.** `sys.stdout.reconfigure(encoding='utf-8')` is called at startup. All file I/O uses `encoding='utf-8'` or `utf-8-sig` for CSV.
- **Windows-primary.** CI runs on `windows-latest`. `pywin32` is in `requirements.txt` but not `requirements-linux.txt`.
- **`core/config.ini` is auto-generated.** The built-in version is the template; user edits live alongside it. Config validation converts types (tuples, booleans, Templates) in `cfg.validate()`.
- **Logging is dual-output.** Stream (INFO) + file (DEBUG) with color coding. Filter excludes third-party library logs.
- **`mei_path()` for packaged files.** Returns `sys._MEIPASS` path when frozen (PyInstaller), otherwise relative path. Use it to reference `data/` and `image/` assets.
- **Genre CSVs must be UTF-8-BOM.** If editing in Excel, save as UTF-8-BOM or parsing fails silently.
- **Test data auto-updates locally.** When a crawler test fails outside CI, `online.dump(file)` overwrites the fixture JSON. This is intentional for keeping fixtures current.

## Adding a crawler

1. Create `web/<name>.py` with `parse_data(info: MovieInfo)`.
2. Raise `MovieNotFoundError` or `MovieDuplicateError` on failure.
3. Add `<name>` to the appropriate `[CrawlerSelect]` list in `core/config.ini`.
4. Add test fixture `unittest/data/{avid} (<name>).json` (copy format from existing files).
5. Run `pytest unittest/test_crawlers.py --only <name>` to verify.
