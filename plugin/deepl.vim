" vim-deepl - DeepL translation and vocabulary trainer for Vim
" SPDX-License-Identifier: LGPL-3.0-only
" Copyright (c) 2025 Romariozh

let g:deepl_version = '0.9.0-pre'

" Prevent double loading
if exists('g:loaded_deepl')
  finish
endif
let g:loaded_deepl = 1

" Check for +job and +channel
if !has('job') || !has('channel')
  echohl WarningMsg
  echom 'vim-deepl: Vim is compiled without +job or +channel. Plugin disabled.'
  echohl None
  finish
endif

" Set default helper path (can be overridden in vimrc)
if !exists('g:deepl_helper_path')
  let g:deepl_helper_path = expand('<sfile>:p:h:h') . '/python/deepl_helper.py'
endif

" Set default dictionary location (XDG compliant)
if !exists('g:deepl_dict_path_base')
  let g:deepl_dict_path_base = expand('~/.local/share/vim-deepl/dict')
endif

" Подсветка направлений перевода в буфере логов
augroup DeeplLogHighlights
  autocmd!
  autocmd BufWinEnter,BufEnter __DeepL_Translation__ call s:deepl_apply_log_highlights()
augroup END

function! s:deepl_apply_log_highlights() abort
  if exists('b:deepl_log_hl_applied') && b:deepl_log_hl_applied
    return
  endif
  let b:deepl_log_hl_applied = 1

  highlight default DeeplHeader cterm=bold ctermfg=114 gui=bold

  " TRN:
  highlight default DeeplTRN cterm=bold ctermfg=223 gui=bold

  call matchadd('DeeplHeader', '^#\d\+\s\+\[.\{-}\]$')

  call matchadd('DeeplTRN', '^TRN:.*$')
endfunction

" Green background label for 'all done'
highlight default DeeplTrainerAllDone ctermfg=0 ctermbg=2 guifg=#000000 guibg=#00aa00

" === Key mappings ===
" Normal: translate word under cursor
nnoremap <silent> <F2>  :call deepl#translate_word()<CR>
" Visual: translate selection (word / phrase / long text)
vnoremap <silent> <F2>  y:call deepl#translate_from_visual()<CR>

" Cycle word source language (EN/DA)
nnoremap <silent> <F3>  :call deepl#cycle_word_src_lang()<CR>
" Cycle target language (RU/EN/DA/...)
nnoremap <silent> <S-F3>  :call deepl#cycle_target_lang()<CR>
" Show MW definitions for the word under the cursor
nnoremap <silent> <leader>d :call deepl#show_defs()<CR>

nnoremap <silent> <leader>s :DeepLStudyToggle<CR>


" Trainer command
command! DeepLTrainerStart call deepl#trainer_start()

" Study UI commands
"command! DeepLStudyStart  silent! call deepl#ui#ensure() | silent! call deepl#trainer_start()
command! DeepLStudyStart  call deepl#ui#ensure() | call deepl#trainer_start()

command! DeepLStudyClose  call deepl#ui#close()
" Toggle Study UI (start if closed, close if open)
command! DeepLStudyToggle call deepl#ui#toggle()
" Ignore current trainer entry (explicit command, no keymap)
command! DeepLEntryIgnore call DeepLTrainerIgnore() 

"Aliases
command! DLStudyStart  DeepLStudyStart
command! DLStudyClose  DeepLStudyClose
command! DLStudyToggle DeepLStudyToggle
command! DLIgnore      DeepLEntryIgnore


augroup deepl_hl
  autocmd!
  autocmd VimEnter,ColorScheme * call deepl#hl#apply_trainer()
augroup END

