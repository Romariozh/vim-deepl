# vim-deepl

![vim-deepl banner](assets/banner.png)

DeepL-powered translation and vocabulary trainer for Vim.

Translate words and phrases directly from your editor, build your dictionary
automatically, and practice inside Vim using spaced repetition.

Works asynchronously in Vim 8+ using job channels. Full DeepL API support.

## License

This project is licensed under the **GNU Lesser General Public License v3.0 only (LGPL-3.0-only)**.  
See the [LICENSE] file for the full text.

## ✨ Features

- Translate word under cursor → popup result
- Short selections (1–3 words) become vocabulary units
- Long selections (4+ words) open a history window
- Dictionary stored locally and reused offline
- Trainer window to learn weakest items first
- Multi-language support (EN ⇄ DA → RU)
- Key mappings for fast workflow

## 🔌 Installation

Requires:

- Vim 8+
- Python 3
- DeepL API key

Using **vim-plug**:

'''vim'''
Plug 'Romariozh/vim-deepl'

## Architecture Overview

                ┌────────────────────────┐
                │        Vim Editor      │
                │  (vim-deepl plugin)    │
                └─────────────┬──────────┘
                              │
                              │ HTTP (curl + job_start)
                              ▼
                ┌────────────────────────┐
                │     FastAPI Backend    │
                │       (dict_api.py)    │
                └─────────────┬──────────┘
                              │
                              │ Python function calls
                              ▼
                ┌────────────────────────┐
                │    Dictionary Engine   │
                │   (deepl_helper.py)    │
                └─────────────┬──────────┘
                              │
                              │ SQL (sqlite3)
                              ▼
                ┌────────────────────────┐
                │       vocab.db         │
                │   (SQLite dictionary)  │
                └────────────────────────┘

### Flow Summary

- Vim sends translation/training requests via HTTP
- FastAPI receives request and dispatches to Python logic
- `deepl_helper.py` reads/writes entries in `vocab.db`
- SQLite stores translations, usage stats, training metadata
- Vim displays results in popup or translation window

### Local API (FastAPI)

Env:
- DEEPL_API_KEY=...
- MW_SD3_API_KEY=...

Run (systemd):
sudo systemctl daemon-reload
sudo systemctl restart vim-dict.service
sudo journalctl -u vim-dict.service -f

Examples:
curl -s http://127.0.0.1:8787/translate/word \
  -H 'Content-Type: application/json' \
  -d '{"term":"banana","target_lang":"RU","src_hint":"EN","context":"I went to the store yesterday."}'

curl -s http://127.0.0.1:8787/translate/selection \
  -H 'Content-Type: application/json' \
  -d '{"text":"hello world","target_lang":"RU","src_hint":"EN"}'

### New features

- Merriam-Webster definitions using **sd3 API endpoint**
- SQLite tables for storing MW definitions by part of speech
- DeepL word translation with **context support** for higher accuracy
