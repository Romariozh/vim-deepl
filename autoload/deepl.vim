" =======================================================
" DeepL async translation + dictionary cache + history
" Works in classic Vim 8/9 (job_start + channels)
"
" SPDX-License-Identifier: LGPL-3.0-only
" Copyright (c) 2025 Romariozh
" =======================================================

" autoload/deepl.vim - core logic for vim-deepl

if exists('g:loaded_deepl_autoload')
  finish
endif
let g:loaded_deepl_autoload = 1

if !exists('g:deepl_backend')
  let g:deepl_backend = 'python'
endif

if !exists('g:deepl_api_base')
  let g:deepl_api_base = 'http://127.0.0.1:8787'
endif

" You can keep global config defaults here if needed, but plugin file already
" sets g:deepl_helper_path and g:deepl_dict_path_base.

" 1) API key from environment (export DEEPL_API_KEY="token")
if exists('$DEEPL_API_KEY')
  let g:deepl_api_key = $DEEPL_API_KEY
else
  let g:deepl_api_key = ''
endif

" 2) Target language cycle for selection translation
let g:deepl_target_lang = 'RU'
let g:deepl_lang_cycle = ['RU', 'EN', 'DA']

" -------------------------------------------------------
" Global variables for the trainer
let g:deepl_trainer_bufnr = -1
let g:deepl_pending_train = ''
let g:deepl_trainer_current = {}

" -------------------------------------------------------
" Utility: clear command-line message
function! ClearTranslation(timer_id) abort
  echo ''
  redraw!
endfunction

" -------------------------------------------------------
" Cycle target language
function! deepl#cycle_target_lang() abort
  let l:list = g:deepl_lang_cycle
  let l:idx = index(l:list, g:deepl_target_lang)
  if l:idx < 0
    let g:deepl_target_lang = l:list[0]
  else
    let g:deepl_target_lang = l:list[(l:idx + 1) % len(l:list)]
  endif
  echo "DeepL target_lang = " . g:deepl_target_lang
endfunction

" -------------------------------------------------------
" Popup helper for word translations
if has('popupwin')
  function! s:DeepLShowPopup(msg) abort
    " Close previous popup if it is still open
    if exists('g:deepl_popup_id') && g:deepl_popup_id != 0
      call popup_close(g:deepl_popup_id)
    endif

    let l:opts = {
          \ 'line': 'cursor+1',
          \ 'col': 'cursor',
          \ 'padding': [0, 1, 0, 1],
          \ 'border': [1, 1, 1, 1],
          \ 'borderchars': ['-','|','-','|','+','+','+','+'],
          \ 'borderhighlight': ['Comment'],
          \ 'highlight': 'Pmenu',
          \ 'time': 4000
          \ }

    let g:deepl_popup_id = popup_create([a:msg], l:opts)
  endfunction
else
  " Fallback if popupwin is not available
  function! s:DeepLShowPopup(msg) abort
    echo a:msg
    let g:trans_timer = timer_start(4000, 'ClearTranslation', {'oneshot': 1})
  endfunction
endif

" -------------------------------------------------------
" Define a “source language” for the dictionary

" Source language of words for the word dictionary (EN / DA)
let g:deepl_word_src_lang = 'EN'
let g:deepl_word_src_cycle = ['EN', 'DA']

function! deepl#cycle_word_src_lang() abort
  let l:list = g:deepl_word_src_cycle
  let l:idx = index(l:list, g:deepl_word_src_lang)
  if l:idx < 0
    let g:deepl_word_src_lang = l:list[0]
  else
    let g:deepl_word_src_lang = l:list[(l:idx + 1) % len(l:list)]
  endif
  echo "DeepL WORD src = " . g:deepl_word_src_lang . " → RU"
endfunction

" -------------------------------------------------------
" Statusline indicator for DeepL (variant 1: last word)
function! DeepLStatusWord() abort
  " If there is no last translated word — return empty string
  if !exists('g:deepl_last_word')
    return ''
  endif

  let l:text = g:deepl_last_word.text
  let l:is_cache = g:deepl_last_word.from_cache

  " Limit length to 20 characters so the statusline does not break
  if strlen(l:text) > 20
    let l:text = strpart(l:text, 0, 17) . "…"
  endif

  return printf('[DL:%s %s]', l:is_cache ? 'D' : 'A', l:text)
endfunction

" -------------------------------------------------------
" DeepL status: current target language for lightline
" (this overrides any previous DeepLStatus definition)
function! deepl#status() abort
  if !exists('g:deepl_target_lang') || empty(g:deepl_target_lang)
    return ''
  endif
  " DL:RU / DL:EN / DL:DA
  return '[DL:' . g:deepl_target_lang . ']'
endfunction

" =======================================================
" DeepL Trainer 
" =======================================================

" -------------------------------------------------------
" Output / error handlers for "train" mode

function! s:DeepLTrainOut(channel, msg) abort
  if empty(a:msg)
    return
  endif
  let g:deepl_pending_train .= a:msg
endfunction

function! s:DeepLTrainErr(channel, msg) abort
  if !empty(a:msg)
    echo "deepl_helper stderr(train): " . a:msg
  endif
endfunction

function! s:DeepLTrainExit(channel, status) abort
  let l:json_str = g:deepl_pending_train
  let g:deepl_pending_train = ''

  if empty(l:json_str)
    return
  endif

  try
    let l:res = json_decode(l:json_str)
  catch
    echo "JSON decode error (train): " . l:json_str
    return
  endtry

  if !empty(get(l:res, 'error', ''))
    echo "Trainer error: " . l:res.error
    return
  endif

  let g:deepl_trainer_current = l:res

  " 0 = hide translation, only show the word
  call DeepLTrainerRender(0)
endfunction

" -------------------------------------------------------
" Render the contents of the trainer buffer

function! DeepLTrainerRender(show_translation) abort
  if g:deepl_trainer_bufnr <= 0 || !bufexists(g:deepl_trainer_bufnr)
    return
  endif

  let l:res = g:deepl_trainer_current
  if empty(l:res)
    return
  endif

  let l:word  = get(l:res, 'word', '')
  let l:tr    = get(l:res, 'translation', '')
  let l:src   = get(l:res, 'src_lang', '')
  let l:lang  = get(l:res, 'target_lang', 'RU')
  let l:count = get(l:res, 'count', 0)
  let l:hard  = get(l:res, 'hard', 0)

  let l:stats = get(l:res, 'stats', {})
  let l:total    = get(l:stats, 'total', 0)
  let l:mastered = get(l:stats, 'mastered', 0)
  let l:thresh   = get(l:stats, 'mastery_threshold', 0)
  let l:percent  = get(l:stats, 'mastery_percent', 0)

  let l:filter = exists('g:deepl_word_src_lang') ? g:deepl_word_src_lang : l:src

  let l:lines = []

  call add(l:lines, printf('DeepL Trainer (%s → %s) — unit from %s',
        \ l:filter, l:lang, l:src))
  call add(l:lines, printf('Unit: %s', l:word))

  if a:show_translation
    call add(l:lines, printf('Translation: %s', l:tr))
  else
    call add(l:lines, 'Translation: ???   (press "s" to show)')
  endif

  call add(l:lines, '')
  call add(l:lines, printf('Count: %d   Hard: %d', l:count, l:hard))

  " Progress: how many words reached the given count
  if l:total > 0 && l:thresh > 0
    call add(l:lines,
          \ printf('Progress: %d/%d words mastered (count ≥ %d) — %d%%',
          \ l:mastered, l:total, l:thresh, l:percent))
  endif

  call add(l:lines, 'Keys: n - next word   s - show translation   x - mark "don''t know"   d - never show again   q - quit')


  " Write to the buffer
  let l:curbuf = bufnr('%')
  if l:curbuf != g:deepl_trainer_bufnr
    execute 'buffer' g:deepl_trainer_bufnr
  endif

  setlocal modifiable
  call setline(1, l:lines)
  if line('$') > len(l:lines)
    execute (len(l:lines)+1) . ',$delete'
  endif
  setlocal nomodifiable
endfunction

" -------------------------------------------------------
" Functions: Start, Next, Show, DeepLTrainerMarkHard, DeepLTrainerShow

function! DeepLTrainerNext() abort
  if g:deepl_trainer_bufnr <= 0 || !bufexists(g:deepl_trainer_bufnr)
    echo "Trainer window is not open. Use :DeepLTrainerStart"
    return
  endif

  " Take current source language from your toggle
  if !exists('g:deepl_word_src_lang') || empty(g:deepl_word_src_lang)
    let l:src_filter = 'EN'
  else
    let l:src_filter = g:deepl_word_src_lang      " EN or DA
  endif

  let g:deepl_pending_train = ''

  if g:deepl_backend ==# 'http'
    " HTTP backend: /train/next
    let l:payload = json_encode({'src_filter': l:src_filter})
    let l:cmd = [
          \ 'curl', '-sS',
          \ '-X', 'POST',
          \ '-H', 'Content-Type: application/json',
          \ '-d', l:payload,
          \ g:deepl_api_base . '/train/next',
          \ ]
  else
    " Legacy python backend
    let l:cmd = [
          \ 'python3',
          \ g:deepl_helper_path,
          \ 'train',
          \ g:deepl_dict_path_base,
          \ l:src_filter,
          \ ]
  endif

  call job_start(l:cmd, {
        \ 'out_cb': function('s:DeepLTrainOut'),
        \ 'err_cb': function('s:DeepLTrainErr'),
        \ 'exit_cb': function('s:DeepLTrainExit'),
        \ 'out_mode': 'raw',
        \ 'err_mode': 'raw',
        \ })
endfunction

function! DeepLTrainerMarkHard() abort
  if empty(g:deepl_trainer_current)
    return
  endif

  let l:word = get(g:deepl_trainer_current, 'word', '')
  if empty(l:word)
    return
  endif

  " Determine dictionary language: EN/DA
  if exists('g:deepl_word_src_lang') && !empty(g:deepl_word_src_lang)
    let l:src = g:deepl_word_src_lang
  else
    let l:src = get(g:deepl_trainer_current, 'src_lang', 'EN')
  endif

  " Asynchronously mark the word as "hard" in Python
  if g:deepl_backend ==# 'http'
    let l:payload = json_encode({'word': l:word, 'src_filter': l:src})
    let l:cmd = [
          \ 'curl', '-sS',
          \ '-X', 'POST',
          \ '-H', 'Content-Type: application/json',
          \ '-d', l:payload,
          \ g:deepl_api_base . '/train/mark_hard',
          \ ]
  else
    let l:cmd = [
          \ 'python3',
          \ g:deepl_helper_path,
          \ 'mark_hard',
          \ g:deepl_dict_path_base,
          \ l:src,
          \ l:word,
          \ ]
  endif

  call job_start(l:cmd, {
        \ 'out_cb': {ch, msg -> 0},
        \ 'err_cb': {ch, msg -> execute('echo "deepl_helper stderr(mark_hard): ".msg')},
        \ 'exit_cb': {ch, st -> 0},
        \ 'out_mode': 'raw',
        \ 'err_mode': 'raw',
        \ })

  " Locally increment hard and re-render (with translation shown)
  let l:hard = get(g:deepl_trainer_current, 'hard', 0) + 1
  let g:deepl_trainer_current.hard = l:hard
  call DeepLTrainerRender(1)
endfunction

function! DeepLTrainerIgnore() abort
  if empty(g:deepl_trainer_current)
    return
  endif

  let l:word = get(g:deepl_trainer_current, 'word', '')
  if empty(l:word)
    return
  endif

  " Determine dictionary language: EN/DA
  if exists('g:deepl_word_src_lang') && !empty(g:deepl_word_src_lang)
    let l:src = g:deepl_word_src_lang
  else
    let l:src = get(g:deepl_trainer_current, 'src_lang', 'EN')
  endif

  " Asynchronously mark word as ignored in Python
  if g:deepl_backend ==# 'http'
    let l:payload = json_encode({'word': l:word, 'src_filter': l:src})
    let l:cmd = [
          \ 'curl', '-sS',
          \ '-X', 'POST',
          \ '-H', 'Content-Type: application/json',
          \ '-d', l:payload,
          \ g:deepl_api_base . '/train/mark_ignore',
          \ ]
  else
    let l:cmd = [
          \ 'python3',
          \ g:deepl_helper_path,
          \ 'ignore',
          \ g:deepl_dict_path_base,
          \ l:src,
          \ l:word,
          \ ]
  endif

  call job_start(l:cmd, {
        \ 'out_cb': {ch, msg -> 0},
        \ 'err_cb': {ch, msg -> execute('echo "deepl_helper stderr(ignore): ".msg')},
        \ 'exit_cb': {ch, st -> 0},
        \ 'out_mode': 'raw',
        \ 'err_mode': 'raw',
        \ })

  " Optionally set local flag and immediately go to the next word
  let g:deepl_trainer_current.ignore = 1

  " Load next word right away
  call DeepLTrainerNext()
endfunction

function! DeepLTrainerShow() abort
  if empty(g:deepl_trainer_current)
    return
  endif
  " 1 = show translation
  call DeepLTrainerRender(1)
endfunction

function! deepl#trainer_start() abort
  " Trainer also does not need API key when using HTTP backend.
  if g:deepl_backend !=# 'http' && empty(g:deepl_api_key)
    echo "Error: DEEPL_API_KEY is not set"
    return
  endif

  if !has('job')
    echo "Error: this Vim is compiled without +job"
    return
  endif

  if !filereadable(g:deepl_helper_path)
    echo "Error: deepl_helper.py not found: " . g:deepl_helper_path
    return
  endif

  " Open trainer window at the bottom with fixed height 7
  botright 7split __DeepL_Trainer__
  let g:deepl_trainer_bufnr = bufnr('%')

  setlocal buftype=nofile bufhidden=wipe noswapfile nobuflisted
  setlocal wrap linebreak
  setlocal nonumber norelativenumber
  setlocal winfixheight          " <=== fix height at 7 lines

  setlocal modifiable

  " Local key mappings in trainer buffer
  nnoremap <silent> <buffer> q :bd!<CR>
  nnoremap <silent> <buffer> n :call DeepLTrainerNext()<CR>
  nnoremap <silent> <buffer> s :call DeepLTrainerShow()<CR>
  nnoremap <silent> <buffer> x :call DeepLTrainerMarkHard()<CR>
  nnoremap <silent> <buffer> d :call DeepLTrainerIgnore()<CR>

  setlocal nomodifiable

  " Immediately load the first word
  call DeepLTrainerNext()
endfunction

" =======================================================
" Async WORD translation (job_start + out_cb/exit_cb)
" =======================================================

let g:deepl_pending_word = ''

function! s:DeepLWordOut(channel, msg) abort
  " out_mode=raw: msg can contain newline(s), accumulate
  if empty(a:msg)
    return
  endif
  let g:deepl_pending_word .= a:msg
endfunction

function! s:DeepLWordErr(channel, msg) abort
  " You can log or show errors from stderr if needed
  if !empty(a:msg)
    echo "deepl_helper stderr: " . a:msg
  endif
endfunction

function! s:DeepLWordExit(channel, status) abort
  let l:json_str = g:deepl_pending_word
  let g:deepl_pending_word = ''

  if empty(l:json_str)
    return
  endif

  try
    let l:res = json_decode(l:json_str)
  catch
    echo "JSON error (word): " . l:json_str
    return
  endtry

  if !empty(get(l:res, 'error', ''))
    echo "Error: " . l:res.error
    return
  endif

  let l:from_cache = get(l:res, 'from_cache', 0)
  let l:text       = get(l:res, 'text', '')
  let l:src_lang   = get(l:res, 'detected_source_lang', '')

  let l:prefix = l:from_cache ? 'DL:D' : 'DL:A'
  if !empty(l:src_lang)
    let l:prefix .= '[' . l:src_lang . '] '
  endif

  let l:msg = l:prefix . l:text

  let g:deepl_last_word = {
      \ 'text': l:text,
      \ 'from_cache': l:from_cache
      \ }

  call s:DeepLShowPopup(l:msg)
endfunction

" -------------------------------------------------------
"  HTTP POST
function! deepl#HttpPost(path, payload) abort
  let l:url = g:deepl_api_base . a:path
  let l:json = json_encode(a:payload)
  let l:cmd = ['curl', '-sS', '-X', 'POST', '-H', 'Content-Type: application/json', '-d', l:json, l:url]
  let l:out = system(l:cmd)
  if v:shell_error != 0
    throw 'DeepL HTTP backend error: ' . l:out
  endif
  return json_decode(l:out)
endfunction

" -------------------------------------------------------
function! deepl#translate_word()  abort
  let l:word = expand('<cword>')
  if empty(l:word)
    echo "No word under cursor"
    return
  endif
  call DeepLTranslateUnit(l:word)
endfunction

" -------------------------------------------------------
" Translate arbitrary unit (word or short phrase) and store it in dictionary.
function! DeepLTranslateUnit(text) abort
  " For python backend we still require DEEPL_API_KEY,
  " but HTTP backend does not need it inside Vim.
  if g:deepl_backend !=# 'http' && empty(g:deepl_api_key)
    echo "Error: DEEPL_API_KEY is not set"
    return
  endif

  if !has('job')
    echo "Error: this Vim is compiled without +job"
    return
  endif

  if !filereadable(g:deepl_helper_path)
    echo "Error: deepl_helper.py not found: " . g:deepl_helper_path
    return
  endif

  let l:unit = s:DeepLCleanUnit(a:text)
  if empty(l:unit)
    echo "Empty text"
    return
  endif

  let l:dict_base = g:deepl_dict_path_base    " ~/.vim_deepl_dict
  let l:target    = 'RU'                      " learn only RU
  let l:src_hint  = g:deepl_word_src_lang     " EN or DA

  let g:deepl_pending_word = ''

  " Build command depending on backend
  if g:deepl_backend ==# 'http'
    " HTTP backend: call FastAPI /translate/word endpoint via curl
    let l:payload = json_encode({
          \ 'term': l:unit,
          \ 'target_lang': l:target,
          \ 'src_hint': l:src_hint,
          \ })
    let l:cmd = [
          \ 'curl', '-sS',
          \ '-X', 'POST',
          \ '-H', 'Content-Type: application/json',
          \ '-d', l:payload,
          \ g:deepl_api_base . '/translate/word',
          \ ]
  else
    " Legacy python backend
    let l:cmd = [
          \ 'python3',
          \ g:deepl_helper_path,
          \ 'word',
          \ l:unit,
          \ l:dict_base,
          \ l:target,
          \ l:src_hint,
          \ ]
  endif

  call job_start(l:cmd, {
        \ 'out_cb': function('s:DeepLWordOut'),
        \ 'err_cb': function('s:DeepLWordErr'),
        \ 'exit_cb': function('s:DeepLWordExit'),
        \ 'out_mode': 'raw',
        \ 'err_mode': 'raw',
        \ })
endfunction

" -------------------------------------------------------
" Strip trailing punctuation for units (one char)
function! s:DeepLCleanUnit(text) abort
  let l:unit = trim(a:text)
  " Remove exactly one trailing punctuation if present
  let l:unit = substitute(l:unit, '[.!?,:;…]\s*$', '', '')
  return l:unit
endfunction

" =======================================================
" Async SELECTION translation → history window
" =======================================================

let g:deepl_pending_sel = ''

function! s:DeepLSelOut(channel, msg) abort
  if empty(a:msg)
    return
  endif
  let g:deepl_pending_sel .= a:msg
endfunction

function! s:DeepLSelErr(channel, msg) abort
  if !empty(a:msg)
    echo "deepl_helper stderr(sel): " . a:msg
  endif
endfunction

function! s:DeepLSelExit(channel, status) abort
  let l:json_str = g:deepl_pending_sel
  let g:deepl_pending_sel = ''

  if empty(l:json_str)
    return
  endif

  try
    let l:res = json_decode(l:json_str)
  catch
    echo "JSON decode error (sel): " . l:json_str
    return
  endtry

  " Error from helper script
  if !empty(get(l:res, 'error', ''))
    echo "Error: " . l:res.error
    return
  endif

  " Extract fields
  let l:ts       = get(l:res, 'timestamp', '')
  let l:lang     = get(l:res, 'target_lang', g:deepl_target_lang)
  let l:src      = get(l:res, 'source', '')
  let l:tr       = get(l:res, 'text', '')

  " Normalize whitespace before comparing.
  let l:src_norm = substitute(trim(l:src), '\s\+', ' ', 'g')
  let l:tr_norm  = substitute(trim(l:tr),  '\s\+', ' ', 'g')

  " If DeepL returns effectively the same text, it is probably code or non-translatable.
  " Avoid polluting history in that case.
  if l:src_norm ==# l:tr_norm
    echo "DeepL: no change (probably code or non-translatable text)"
    return
  endif

  " -------------------------------
  " Count number of words in the source text
  " -------------------------------
  " split() by spaces, filter out empty elements
  let l:words = filter(split(l:src), 'v:val !=# ""')
  let l:wc    = len(l:words)

  " Try to get detected source language (if DeepL returned it)
  let l:src_lang = get(l:res, 'detected_source_lang', '')

  " Build language tag like [EN -> RU] or fallback to [RU]
  if empty(l:src_lang)
    let l:lang_tag = l:lang
  else
    let l:lang_tag = l:src_lang . ' -> ' . l:lang
  endif

  " Initialize history counter
  if !exists('g:deepl_request_counter')
    let g:deepl_request_counter = 0
  endif
  let g:deepl_request_counter += 1
  let l:index = g:deepl_request_counter

  " Fallback if API didn’t send timestamp
  if empty(l:ts)
    let l:ts = strftime('%Y-%m-%d %H:%M:%S')
  endif
  
  " Normalize source/translation to a single logical line
  let l:src_clean = substitute(l:src, '\n', ' ', 'g')
  let l:src_clean = substitute(l:src_clean, '\s\+', ' ', 'g')

  let l:tr_clean = substitute(l:tr, '\n', ' ', 'g')
  let l:tr_clean = substitute(l:tr_clean, '\s\+', ' ', 'g')

  " Form history entry (so it is still saved)
  let g:deepl_last_entry =
        \ '#' . l:index . ' [' . l:ts . '] [' . l:lang_tag . "]\n"
        \ . 'SRC: ' . l:src_clean . "\n"
        \ . 'TRN: ' . l:tr_clean . "\n"

  " -------------------------------
  " BRANCH 1: up to 3 words -> popup
  " -------------------------------
  if l:wc <= 3
    " Use existing popup helper
    if exists('*s:DeepLShowPopup')
      call s:DeepLShowPopup('DeepL: ' . l:tr)
    else
      " Just in case, simple fallback
      echo 'DeepL: ' . l:tr
    endif
    " Do not open the history window, but the entry is already saved
    return
  endif

  " -------------------------------
  " BRANCH 2: 4+ words -> history window (as before)
  " -------------------------------
  call DeepLShowInWindow()
endfunction

function! deepl#translate_from_visual() abort
  " Basic checks
  if g:deepl_backend !=# 'http' && empty(g:deepl_api_key)
    echo "Error: DEEPL_API_KEY is not set"
    return
  endif

  if !has('job')
    echo "Error: this Vim is compiled without +job"
    return
  endif

  if !filereadable(g:deepl_helper_path)
    echo "Error: deepl_helper.py not found: " . g:deepl_helper_path
    return
  endif

  " Get text from default register (Visual mapping does: y:call ...)
  let l:text = getreg('"')
  if empty(l:text)
    echo "Selection is empty"
    return
  endif

  " Same target and src_hint logic as for single word translation
  let l:dict_base = g:deepl_dict_path_base    " ~/.vim_deepl_dict
  let l:target    = 'RU'                      " learn only RU (your current logic)
  let l:src_hint  = g:deepl_word_src_lang     " EN or DA

  " Flatten newlines for safe CLI argument and word counting
  let l:clean = substitute(l:text, '\n', ' ', 'g')

  " Count words in the selection
  let l:words = filter(split(l:clean), 'v:val !=# ""')
  let l:wc    = len(l:words)

  " --- Case 1: 1–3 words -> treat as vocabulary unit (word/phrase) ---
  if l:wc > 0 && l:wc <= 3
    " Use the same logic as for word under cursor (dictionary + popup + trainer)
    call DeepLTranslateUnit(l:clean)
    return
  endif

  " --- Case 2: 4+ words -> selection translation + history window ---
  let g:deepl_pending_sel = ''

    if g:deepl_backend ==# 'http'
      " HTTP backend for long selection
      let l:payload = json_encode({
        \ 'text': l:text,
        \ 'target_lang': l:target,
        \ 'src_hint': l:src_hint,
        \ })
      let l:cmd = [
        \ 'curl', '-sS',
        \ '-X', 'POST',
        \ '-H', 'Content-Type: application/json',
        \ '-d', l:payload,
        \ g:deepl_api_base . '/translate/selection',
        \ ]
    else
      " Legacy python backend (fallback)
      let l:cmd = [
        \ 'python3',
        \ g:deepl_helper_path,
        \ 'selection',
        \ l:text,
        \ g:deepl_dict_path_base,
        \ l:target,
        \ l:src_hint,
        \ ]
    endif

    call job_start(l:cmd, {
       \ 'out_cb': function('s:DeepLSelOut'),
       \ 'err_cb': function('s:DeepLSelErr'),
       \ 'exit_cb': function('s:DeepLSelExit'),
       \ 'out_mode': 'raw',
       \ 'err_mode': 'raw',
       \ })
endfunction

" =======================================================
" HISTORY WINDOW
" =======================================================

function! DeepLShowInWindow() abort
  if !exists('g:deepl_last_entry') || empty(g:deepl_last_entry)
    echo "No translation to show"
    return
  endif

  " Remember current window (main text) to restore focus later
  let l:curwin  = winnr()

  let l:lines   = split(g:deepl_last_entry, "\n")
  let l:bufname = "__DeepL_Translation__"
  let l:bufnr   = bufnr(l:bufname)

  " --- Open or reuse bottom window with height 5 lines ---
  let l:win_height = 7 

  if l:bufnr == -1
    " Buffer does not exist yet — create new split at the bottom
    execute 'botright ' . l:win_height . 'new'
    let l:bufnr = bufnr('%')
    execute 'file ' . l:bufname
  else
    " Buffer already exists
    let l:wnr = bufwinnr(l:bufnr)
    if l:wnr == -1
      " Buffer exists but is not shown — open in a new bottom split
      execute 'botright ' . l:win_height . 'split'
      execute 'buffer ' . l:bufnr
    else
      " Buffer is already visible — just jump to its window
      execute l:wnr . 'wincmd w'
      execute 'resize ' . l:win_height
    endif
  endif

  " Local options for history window
  setlocal buftype=nofile bufhidden=hide nobuflisted noswapfile
  setlocal wrap linebreak
  setlocal nonumber norelativenumber
  setlocal winfixheight          " keep height fixed at 5 lines

  " Buffer-local mapping:
  " q - clear history and close window
  nnoremap <silent> <buffer> q :call DeepLClearHistory()<CR>

  " Make buffer editable
  setlocal modifiable

  " If buffer is empty — write from the beginning,
  " otherwise append at the end with an empty separator line
  if line('$') == 1 && getline(1) ==# ''
    call setline(1, l:lines)
  else
    call append(line('$'), '')
    call append(line('$'), l:lines)
  endif

  " Make non-modifiable to avoid accidental edits
  setlocal nomodifiable

  " Scroll to bottom, then move cursor to the header line of the last entry ("#N [...] [...]").
  " Go to end (latest entry at bottom)
  normal! G

  " Move cursor to header line of the last entry
  let l:pos = search('^#\d\+ ', 'bW')
  if l:pos == 0
    " If not found, keep cursor at bottom
    normal! G
  else
    " Put header line at top of the window
    normal! zt
  endif 
  " Restore focus to the original window (main text)
  if l:curwin > 0 && winnr() != l:curwin
    execute l:curwin . 'wincmd w'
  endif
  " Clear command line & redraw to remove status junk
  silent! echo ""
  silent! redraw!
endfunction

function! DeepLClearHistory() abort
  let l:bufname = "__DeepL_Translation__"
  let l:bufnr   = bufnr(l:bufname)

  " Сбрасываем счётчики истории
  let g:deepl_request_counter = 0
  unlet! g:deepl_last_entry

  " Если буфера нет — просто сообщение
  if l:bufnr == -1
    echo "DeepL history cleared"
    return
  endif

  " Буфер есть, проверим его окно
  let l:wnr = bufwinnr(l:bufnr)

  " Если окно открыто — закрываем его напрямую
  if l:wnr != -1
    execute l:wnr . 'wincmd w'
    bd!
    echo "DeepL history cleared"
    return
  endif

  " Окно закрыто, но буфер существует — очищаем контент
  call setbufline(l:bufnr, 1, [''])
  call deletebufline(l:bufnr, 2, '$')

  echo "DeepL history cleared"
endfunction

"===========================================================
