" vim-deepl - DeepL translation and vocabulary trainer for Vim
" Version
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

" === Key mappings ===
" Normal: translate word under cursor
nnoremap <silent> <F2>  :call deepl#translate_word()<CR>
" Visual: translate selection (word / phrase / long text)
vnoremap <silent> <F2>  :call deepl#translate_from_visual()<CR>

" Cycle word source language (EN/DA)
nnoremap <silent> <F3>  :call deepl#cycle_word_src_lang()<CR>
" Cycle target language (RU/EN/DA/...)
nnoremap <silent> <S-F3>  :call deepl#cycle_target_lang()<CR>

" Trainer command
command! DeepLTrainerStart call deepl#trainer_start()
