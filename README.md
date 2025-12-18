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
- Trainer window with SRS (spaced repetition): due → new → hard
- Daily progress: today count + streak
- Context-aware training: uses original text snippet when available
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

## Trainer (SRS)

The project includes a vocabulary trainer based on SRS (spaced repetition) built on top of `entries` + `training_*` tables.

### Data model

- `entries` — vocabulary items (`term`, `translation`, `src_lang`, `dst_lang`, `ignore`, `hard`, ...).
- `detected_raw` is treated as the original context (if present).

- `training_cards` — SRS state per vocabulary item.
- link: `training_cards.entry_id -> entries.id` (unique)
- main fields: `reps`, `lapses`, `ef`, `interval_days`, `due_at`,
  `last_review_at`, `last_grade`, `correct_streak`, `wrong_streak`, `suspended`

- `training_reviews` — review log.
- fields: `card_id`, `ts`, `grade`, `day`

### Picking the next training item

Implemented inside `TrainerService.pick_training_word()` (SRS picker v3):

1) **due**: pick items with `due_at <= now` first  
2) if no due items:
   - pick **new** (an `entries` row without a card yet) with probability `cfg.srs_new_ratio`
   - otherwise pick **hard** (highest `lapses` / `wrong_streak`)
3) ignored items are excluded: `entries.ignore = 1`
4) when a new entry is picked for the first time, a card is created:
   `training_cards(entry_id, due_at=now)`

The returned dict typically includes:
- `card_id`, `entry_id`, `term`, `translation`, `src_lang`, `dst_lang`
- `mode`: `srs_due` / `srs_new` / `srs_hard`
- daily progress: `day`, `today_done`, `streak_days`
- context: `context_raw` (from `entries.detected_raw`, if available)

### Reviewing (grading) an answer

`TrainerService.review_training_card(card_id, grade, now)`:
- inserts a row into `training_reviews`
- updates the SRS state in `training_cards`

`grade` is in range `0..5`.

### Daily progress / streak

Computed from `training_reviews.day`:
- `today_done` — number of reviews for the current date
- `streak_days` — consecutive days (including today) with at least one review

### Checks before pushing

#bash
python3 -m compileall -q ./python
PYTHONPATH=./python pytest -q

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
