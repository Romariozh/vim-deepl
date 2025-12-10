# Changelog

## v0.9.5-pre — SQLite Dictionary + HTTP Backend Integration

### Added
- Added FastAPI-based HTTP backend (`dict_api.py`) for translation, training, and dictionary access.
- Introduced SQLite database (`vocab.db`) as the new storage backend for all dictionary entries.
- Added new API endpoints:
  - `POST /translate/word`
  - `POST /translate/selection`
  - `POST /train/next`
  - `POST /train/mark_hard`
  - `POST /train/mark_ignore`
  - `GET  /entries`
  - `POST /entries`
- Implemented Vim HTTP mode (`g:deepl_backend = 'http'`) using `curl` + job control.
- Improved translation UI:
  - New history window rendering
  - Clean one-line SRC/TRN formatting
  - Smart header anchoring in translation window
  - Automatic resizing of translation pane

### Changed
- Legacy JSON dictionaries replaced with SQLite (`vocab.db`).
- All Python CLI calls removed from Vim logic — replaced by HTTP calls.
- Updated job control logic for word and selection translation inside Vim.

### Removed
- Deprecated JSON dictionary files:
  - `dict_en.json`
  - `dict_da.json`
- Old CLI-based workflow in Vim.

### Migration Notes
- Old JSON dictionaries are no longer used and not automatically migrated.
- Add the following to `.vimrc`:
  ```vim
  let g:deepl_backend = 'http'
  let g:deepl_api_base = 'http://127.0.0.1:8787'
