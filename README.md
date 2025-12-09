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

```vim
Plug 'Romariozh/vim-deepl'

