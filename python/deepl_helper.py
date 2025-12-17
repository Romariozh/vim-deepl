#!/usr/bin/env python3
# vim-deepl - DeepL translation and vocabulary trainer for Vim
# SPDX-License-Identifier: LGPL-3.0-only

from vim_deepl.transport.vim_stdio import run
from vim_deepl.cli.dispatcher import dispatch

if __name__ == "__main__":
    run(dispatch)
