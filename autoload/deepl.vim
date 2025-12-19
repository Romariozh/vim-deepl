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
"  The single access to target/src_hint

function! deepl#TargetLang() abort
  return get(g:, 'deepl_target_lang', 'RU')
endfunction

function! deepl#SrcHint() abort
  return get(g:, 'deepl_word_src_lang', '')
endfunction

" -------------------------------------------------------
"
function! s:deepl_payload_word(term) abort
  return json_encode({
        \ 'term': a:term,
        \ 'target_lang': deepl#TargetLang(),
        \ 'src_hint': deepl#SrcHint(),
        \ })
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
  echo "DeepL WORD src = " . g:deepl_word_src_lang 

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
" Normalize trainer payload coming from backend (SRS v3 + legacy fallback)
function! s:DeepLTrainerNormalize(res) abort
  if type(a:res) != type({})
    return {}
  endif

  let l:r = copy(a:res)

  " Prefer new keys; fall back to legacy ones
  if !has_key(l:r, 'term')
    let l:r.term = get(l:r, 'word', '')
  endif
  if !has_key(l:r, 'dst_lang')
    let l:r.dst_lang = get(l:r, 'target_lang', deepl#TargetLang())
  endif
  if !has_key(l:r, 'mode')
    let l:r.mode = ''
  endif
  if !has_key(l:r, 'context_raw')
    let l:r.context_raw = ''
  endif

  return l:r
endfunction

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

" -------------------------------------------------------
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

  let g:deepl_trainer_current = s:DeepLTrainerNormalize(l:res)
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

  let l:word  = get(l:res, 'term', get(l:res, 'word', ''))
  let l:lang  = get(l:res, 'dst_lang', get(l:res, 'target_lang', deepl#TargetLang()))
  let l:mode  = get(l:res, 'mode', '')
  let l:ctx   = get(l:res, 'context_raw', '')

  let l:tr    = get(l:res, 'translation', '')
  let l:src   = get(l:res, 'src_lang', '')
  let l:count = get(l:res, 'count', 0)
  let l:hard  = get(l:res, 'hard', 0)

  let l:stats = get(l:res, 'stats', {})
  let l:total    = get(l:stats, 'total', 0)
  let l:mastered = get(l:stats, 'mastered', 0)
  let l:thresh   = get(l:stats, 'mastery_threshold', 0)
  let l:percent  = get(l:stats, 'mastery_percent', 0)

  let l:filter = exists('g:deepl_word_src_lang') ? g:deepl_word_src_lang : l:src
  let l:mode_suffix = empty(l:mode) ? '' : (' [' . l:mode . ']')
  let l:lines = []
  " Fallbacks for empty language tags (avoid blank header)
  let l:filter = empty(l:filter) ? 'EN' : l:filter
  let l:src = empty(l:src) ? l:filter : l:src

  call add(l:lines, printf('DeepL Trainer (%s → %s) — unit from %s%s',
        \ l:filter, l:lang, l:src, l:mode_suffix))
  call add(l:lines, printf('Unit: %s', l:word))

  if !empty(l:ctx)
    call add(l:lines, '')
    call add(l:lines, 'Context:')
    call add(l:lines, '  ' . l:ctx)
  endif

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

  " Open trainer window at the bottom with fixed height 8
  botright 8split __DeepL_Trainer__
  let g:deepl_trainer_bufnr = bufnr('%')

  setlocal buftype=nofile bufhidden=wipe noswapfile nobuflisted
  setlocal wrap linebreak
  setlocal nonumber norelativenumber
  setlocal winfixheight          " <=== fix height at 8 lines

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
function! s:deepl_sentence_context() abort
  let l:line = getline('.')
  if empty(l:line)
    return ''
  endif

  let l:col = col('.') - 1
  if l:col < 0
    let l:col = 0
  endif

  " Sentence boundaries (left side) by literal chars: . ! ? : ;
  let l:prefix = strpart(l:line, 0, l:col)

  let l:left_dot = strridx(l:prefix, '.')
  let l:left_exc = strridx(l:prefix, '!')
  let l:left_q   = strridx(l:prefix, '?')
  let l:left_col = strridx(l:prefix, ':')
  let l:left_sem = strridx(l:prefix, ';')

  let l:start = max([l:left_dot, l:left_exc, l:left_q, l:left_col, l:left_sem]) + 1

  " Sentence boundaries (right side) using match() patterns.
  " IMPORTANT: '?' must be escaped as '\?' in Vim regex.
  let l:end = -1
  " IMPORTANT: use literal '?' (NOT '\?') because '\?' is a quantifier in Vim regex.
  for l:pat in ['\.', '!', '?', ':', ';']
    let l:p = match(l:line, l:pat, l:col)
    if l:p != -1 && (l:end == -1 || l:p < l:end)
      let l:end = l:p
    endif
  endfor

  if l:end == -1
    let l:sent = strpart(l:line, l:start)
  else
    let l:sent = strpart(l:line, l:start, (l:end - l:start + 1))
  endif

  let l:sent = trim(l:sent)
  if strchars(l:sent) > 400
    let l:sent = strcharpart(l:sent, 0, 400)
  endif

  return l:sent
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
" HTTP helper: POST JSON and return decoded JSON dict.
function! s:http_post_json(url, payload_dict) abort
  let l:payload = json_encode(a:payload_dict)

  " NOTE: system() expects a String command (not a List) in many Vim builds.
  let l:cmd =
        \ 'curl -sS -X POST ' .
        \ '-H ' . shellescape('Content-Type: application/json') . ' ' .
        \ '-d ' . shellescape(l:payload) . ' ' .
        \ shellescape(a:url)

  let l:out = system(l:cmd)
  if v:shell_error != 0
    throw 'curl failed: ' . l:out
  endif

  try
    return json_decode(l:out)
  catch
    throw 'json_decode failed: ' . l:out
  endtry
endfunction
" -------------------------------------------------------
" Show Merriam-Webster definitions for the word under cursor (if available).
function! deepl#show_defs() abort
  let l:word = expand('<cword>')
  if empty(l:word)
    echo "No word under cursor"
    return
  endif

  " This feature needs HTTP backend because we query FastAPI for mw_definitions.
  if get(g:, 'deepl_backend', '') !=# 'http'
    echo "Definitions popup requires g:deepl_backend='http'"
    return
  endif

  if empty(get(g:, 'deepl_api_base', ''))
    echo "Error: g:deepl_api_base is not set"
    return
  endif
  let l:url = g:deepl_api_base . '/translate/word'

  " Keep consistent with the current DeepL settings.
  let l:target   = deepl#TargetLang()
  let l:src_hint = get(g:, 'deepl_word_src_lang', '')

  " Use sentence under cursor as context (same idea as <F2> word translate).
  let l:ctx = ''
  try
    let l:ctx = s:deepl_sentence_context()
  catch
    let l:ctx = ''
  endtry

  let l:payload = {
        \ 'term': l:word,
        \ 'target_lang': l:target,
        \ 'src_hint': l:src_hint,
        \ 'context': l:ctx,
        \ }

  try
    let l:resp = s:http_post_json(l:url, l:payload)
  catch
    echo "deepl#show_defs: backend request failed"
    return
  endtry

  " FastAPI returns mw_definitions either as dict or null.
  if !has_key(l:resp, 'mw_definitions') || type(l:resp.mw_definitions) != v:t_dict
    echo "No MW definitions for: " . l:word
    return
  endif

  " Prefer server-provided 'source' (canonical token) over expand('<cword>').
  let l:source      = get(l:resp, 'source', l:word)
  let l:translation = get(l:resp, 'text', '')

  " Determine whether context was actually used.
  let l:ctx_used = get(l:resp, 'context_used', v:false)

  " Determine source label.
  let l:from_cache   = get(l:resp, 'from_cache', v:false)
let l:cache_source = get(l:resp, 'cache_source', '')

" Source label:
" - from_cache=true  -> Dictionary (SQLite)
" - from_cache=false -> DeepL API (fresh request)
let l:src_label = l:from_cache ? 'Dictionary' : 'DeepL API'

  " Build right-side tag: SRC + optional CTX
  let l:src_tag = 'SRC: ' . l:src_label
  if l:ctx_used
    let l:src_tag .= ' | CTX'
  endif

  " Left side header: "word → translation"
  let l:left = empty(l:translation) ? l:source : (l:source . ' → ' . l:translation)

  " Single header line: hard right-align src_tag to popup width
  let l:width = get(g:, 'deepl_mw_popup_width', 80)

  " Always keep at least 1 space between left and right parts
  let l:space = l:width - strdisplaywidth(l:left) - strdisplaywidth(l:src_tag)
  if l:space < 1
    let l:space = 1
  endif

  let l:header_line = l:left . repeat(' ', l:space) . l:src_tag

  " Build popup lines
  let l:mw = l:resp.mw_definitions
  let l:lines = [l:header_line, '']

  let l:sections = ['verb', 'noun', 'adjective', 'adverb', 'other']
  for l:sec in l:sections
    let l:defs = get(l:mw, l:sec, [])
    if type(l:defs) == type([]) && len(l:defs) > 0
      call add(l:lines, toupper(l:sec) . ':')
      for l:d in l:defs
        call add(l:lines, '• ' . l:d)
      endfor
      call add(l:lines, '')
    endif
  endfor

  if len(l:lines) <= 2
    call add(l:lines, '(no MW definitions)')
  endif

  call s:deepl_show_defs_buffer(l:lines, '-  MW  -')
endfunction

" -------------------------------------------------------
" Internal helper: show list of lines in a popup or preview window
function! s:deepl_show_defs_buffer(lines, title) abort
  if has('popupwin')
    let width  = get(g:, 'deepl_mw_popup_width', 80)
    let height = len(a:lines) > 20 ? 20 : len(a:lines)

    let l:opts = {
          \ 'title': a:title,
          \ 'minwidth': width,
          \ 'maxwidth': width,
          \ 'minheight': height,
          \ 'maxheight': height,
          \ 'padding': [0, 1, 0, 1],
          \ 'border': [1, 1, 1, 1],
          \ 'borderchars': ['-','|','-','|','+','+','+','+'],
          \ 'borderhighlight': ['Comment'],
          \ 'highlight': 'Pmenu',
          \ 'wrap': v:true,
          \ 'mapping': v:true,
          \ 'filter': function('s:deepl_popup_filter'),
          \ 'shadow': 1,
          \ 'shadowhighlight': 'Pmenu',
          \ 'line': (&lines / 2) - (height / 2),
          \ 'col':  (&columns / 2) - (width  / 2),
          \ }

    let l:popup_id = popup_create(a:lines, l:opts)
    " Word-wrap (do not break words in the middle when possible)
    call win_execute(l:popup_id, 'setlocal linebreak')
    call win_execute(l:popup_id, 'setlocal breakat=\ \	.,;:!?)]}''"')
    call win_execute(l:popup_id, 'setlocal showbreak=↳\ ')
    call s:deepl_defs_popup_apply_hl(l:popup_id)
    return
  endif

  " fallback — preview window, если popupwin нет
  pclose
  belowright pedit MW-Definitions
  setlocal buftype=nofile bufhidden=wipe nobuflisted noswapfile
  setlocal modifiable
  silent %delete _
  call setline(1, a:lines)
  setlocal nomodifiable
  execute 'file [MW] '.a:title
endfunction
" -------------------------------------------------------
function! s:deepl_defs_popup_apply_hl(popup_id) abort
  let l:winid = a:popup_id

  " Bold only the left part of the header line (before SRC:)
  call win_execute(l:winid,
        \ "silent! call matchadd('DeeplPopupHeaderLeft', '^\\zs.*\\ze\\s\\+SRC:', 10)")

  " Source tag colors
  call win_execute(l:winid,
        \ "silent! call matchadd('DeeplPopupSrcDict', 'SRC: Dictionary\\(\\s\\|$\\).*', 20)")
  call win_execute(l:winid,
        \ "silent! call matchadd('DeeplPopupSrcApi',  'SRC: DeepL API\\(\\s\\|$\\).*', 20)")

  " CTX marker highlight (only if present in text)
  call win_execute(l:winid,
        \ "silent! call matchadd('DeeplPopupCtx', '\\<CTX\\>', 30)")
endfunction
" -------------------------------------------------------
function! s:popup_scroll(id, delta) abort
  " Vim without these functions can't scroll popups
  if !exists('*popup_getoptions') || !exists('*popup_setoptions')
    return
  endif

  let l:opts = popup_getoptions(a:id)
  let l:first = get(l:opts, 'firstline', 1)

  let l:new_first = l:first + a:delta
  if l:new_first < 1
    let l:new_first = 1
  endif

  call popup_setoptions(a:id, {'firstline': l:new_first})
endfunction
" -------------------------------------------------------
function! s:deepl_popup_filter(id, key) abort
 
    if a:key ==# "\<Esc>"
    call popup_close(a:id)
    return ''
  endif

  if a:key ==# 'j' || a:key ==# "\<Down>" || a:key ==# "\<ScrollWheelDown>"
    call s:popup_scroll(a:id, 1)
    return ''
  endif

  if a:key ==# 'k' || a:key ==# "\<Up>" || a:key ==# "\<ScrollWheelUp>"
    call s:popup_scroll(a:id, -1)
    return ''
  endif

  return a:key
endfunction
" -------------------------------------------------------
function! deepl#translate_word() abort
  let l:word = expand('<cword>')
  if empty(l:word)
    echo "No word under cursor"
    return
  endif

  " Use current DeepL target lang (your <S-F3> cycle)
  let l:target = deepl#TargetLang()

  " Source hint (EN/DA) for DeepL word mode (your <F3> switch)
  let l:src_hint = get(g:, 'deepl_word_src_lang', '')

  " Context sentence around cursor (must exist; see below)
  let l:ctx = s:deepl_sentence_context()

  call DeepLTranslateUnit(l:word, l:target, l:src_hint, l:ctx)
endfunction
" -------------------------------------------------------
" Translate arbitrary unit (word or short phrase) and store it in dictionary.
function! DeepLTranslateUnit(text, ...) abort
  " For python backend we still require DEEPL_API_KEY,
  " but HTTP backend does not need it inside Vim.
  " Normalize input (unit) and optional args
  let l:unit = a:text
  let l:target   = (a:0 >= 1 && !empty(a:1)) ? a:1 : deepl#TargetLang()
  let l:src_hint = (a:0 >= 2) ? a:2 : get(g:, 'deepl_word_src_lang', '')
  let l:ctx      = (a:0 >= 3) ? a:3 : ''

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
  let g:deepl_pending_word = ''

  " Build command depending on backend
  if g:deepl_backend ==# 'http'
    " HTTP backend: call FastAPI /translate/word endpoint via curl
    let l:payload = json_encode({
          \ 'term': l:unit,
          \ 'target_lang': l:target,
          \ 'src_hint': l:src_hint,
          \ 'context': l:ctx,
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


  " Normalize source/translation to a single logical line
  let l:src_clean = substitute(l:src, '\n', ' ', 'g')
  let l:src_clean = substitute(l:src_clean, '\s\+', ' ', 'g')

  let l:tr_clean = substitute(l:tr, '\n', ' ', 'g')
  let l:tr_clean = substitute(l:tr_clean, '\s\+', ' ', 'g')

  " Form history entry (so it is still saved)
  let g:deepl_last_entry =
        \ '#' . l:index . ' [' . l:lang_tag . "]\n"
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
  let l:target    = deepl#TargetLang()       
  let l:src_hint  = deepl#SrcHint()           " EN or DA

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
  let l:win_height = 8 

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
  " Decide whether to show header (top) or end (bottom)
  normal! G
  let l:header_lnum = search('^#\d\+ ', 'bW')
  if l:header_lnum > 0
    let l:entry_len = line('$') - l:header_lnum + 1
    " Если запись больше высоты окна - 1, показываем низ
    if l:entry_len > (winheight(0) - 1)
      normal! zb
    else
      call cursor(l:header_lnum, 1)
      normal! zt
    endif
  else
    normal! zb
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

" Popup highlight groups (MW/word popup header tags)
highlight default DeeplPopupSrcDict cterm=bold ctermfg=108 gui=bold
highlight default DeeplPopupSrcApi  cterm=bold ctermfg=110 gui=bold
highlight default DeeplPopupCtx     cterm=bold ctermfg=215 gui=bold
highlight default DeeplPopupHeaderLeft cterm=bold gui=bold

"===========================================================
