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

1. Vim sends translation/training requests via HTTP
2. FastAPI receives request and dispatches to Python logic
3. `deepl_helper.py` reads/writes entries in `vocab.db`
4. SQLite stores translations, usage stats, training metadata
5. Vim displays results in popup or translation window

### New features

- Merriam-Webster definitions using **sd3 API endpoint**
- SQLite tables for storing MW definitions by part of speech
- DeepL word translation with **context support** for higher accuracy
