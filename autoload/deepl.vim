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

" Trainer state
let g:deepl_trainer_bufnr = -1
let g:deepl_trainer_current = {}

" Cards excluded for this trainer session (skip list)
let g:deepl_trainer_exclude = []


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

  " If we just reviewed a card, exclude it for this session to prevent sticking
  if get(g:, 'deepl_trainer_last_op', '') ==# 'review'
    let l:rid = get(g:, 'deepl_trainer_last_reviewed_id', 0)
    if l:rid > 0 && index(g:deepl_trainer_exclude, l:rid) < 0
      call add(g:deepl_trainer_exclude, l:rid)
    endif
  endif

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
  let l:day = get(l:res, 'day', '')
  let l:today_done = get(l:res, 'today_done', 0)
  let l:streak_days = get(l:res, 'streak_days', 0)

  let l:stats = get(l:res, 'stats', {})
  let l:total    = get(l:stats, 'total', 0)
  let l:mastered = get(l:stats, 'mastered', 0)
  let l:thresh   = get(l:stats, 'mastery_threshold', 0)
  let l:percent  = get(l:stats, 'mastery_percent', 0)

  " Resolve filter / src labels
  let l:filter = exists('g:deepl_word_src_lang') ? g:deepl_word_src_lang : l:src
  let l:filter = empty(l:filter) ? 'EN' : l:filter
  let l:src = empty(l:src) ? l:filter : l:src
  
  " Pretty label for header modes (API sends raw mode strings)
  let l:mode_label = l:mode
  if l:mode_label ==# 'fallback'
    let l:mode_label = 'all done'
  endif

  let l:mode_suffix = empty(l:mode_label) ? '' : (' [' . l:mode_label . ']')

  " Determine width (prefer actual window width if visible)
  let l:winid = bufwinid(g:deepl_trainer_bufnr)
  let l:width = (l:winid != -1) ? winwidth(l:winid) : &columns
  let l:width = max([40, l:width - 1])
  let l:sep = repeat('-', l:width)

  " Progress bar (ASCII-safe)
  let l:bar_w = max([10, min([24, l:width - 26])])
  let l:fill = float2nr(l:bar_w * (l:percent / 100.0))
  let l:fill = max([0, min([l:bar_w, l:fill])])
  let l:bar = '[' . repeat('#', l:fill) . repeat('-', l:bar_w - l:fill) . ']'

  " Context: show only if it looks like a sentence
  if type(l:ctx) != v:t_string
    let l:ctx = ''
  endif
  if l:ctx !~# '\s' && l:ctx !~# '[\.\!\?,;:]'
    let l:ctx = ''
  endif

  let l:lines = []

  " Header (no legacy Count/Hard)
  call add(l:lines, printf(
        \ ' :Trainer: (%s -> %s)%s Reviewed: %d Run: %dd  ᚨᚲᛚ',
        \ l:filter, l:lang, l:mode_suffix, l:today_done, l:streak_days))
  
  " SRS details line (replaces old Count/Hard line)
  let l:reps   = get(l:res, 'reps', 0)
  let l:lapses = get(l:res, 'lapses', 0)
  let l:wrong  = get(l:res, 'wrong_streak', 0)

  let l:due_raw = get(l:res, 'due_at', '')
  let l:due_s = ''
  if type(l:due_raw) == v:t_number
    let l:due_s = strftime('%Y-%m-%d %H:%M', l:due_raw)
  elseif type(l:due_raw) == v:t_string && l:due_raw =~# '^\d\+$'
    let l:due_s = strftime('%Y-%m-%d %H:%M', str2nr(l:due_raw))
  else
    let l:due_s = string(l:due_raw)
  endif

  call add(l:lines, printf('✅ reps:%d  🔁 lapses:%d ⚠️  wrong:%d  ⏳ due:%s',
        \ l:reps, l:lapses, l:wrong, l:due_s))

  call add(l:lines, l:sep)

  " Card (UNIT + TRN on the same line)
  if a:show_translation
    call add(l:lines, 'UNIT: ' . l:word . '    TRN:  ' . l:tr)
  else
    call add(l:lines, 'UNIT: ' . l:word . '    TRN: [hidden] (s)')
  endif

  " Context (up to 2 lines)
  let l:ctx_lines = s:deepl_wrap(l:ctx, l:width - 6, 3)
  if !empty(l:ctx_lines)
   call add(l:lines, 'CTX:  ' . l:ctx_lines[0])
   if len(l:ctx_lines) > 1 | call add(l:lines, '      ' . l:ctx_lines[1]) | endif
   if len(l:ctx_lines) > 2 | call add(l:lines, '      ' . l:ctx_lines[2]) | endif
  endif
 
  " --- GRAMMAR (MW) ---
  let l:g = get(l:res, 'grammar', {})
  if type(l:g) == v:t_dict && !empty(l:g)
    call add(l:lines, 'GRAMMAR:')

    let l:gw = get(l:g, 'word', '')
    if !empty(l:gw)
      call add(l:lines, '  Word: ' . l:gw)
    endif

    " Stems (как у тебя уже хорошо сделано)
    let l:stems = get(l:g, 'stems', [])
    if type(l:stems) == v:t_list && !empty(l:stems)
      let l:st = 'Stems: ' . join(l:stems, ', ')
      let l:wrapped = s:deepl_wrap(l:st, l:width - 4, 2)
      if !empty(l:wrapped)
        call add(l:lines, '  ' . l:wrapped[0])
        if len(l:wrapped) > 1
          call add(l:lines, '  ' . repeat(' ', strlen('Stems: ')) . l:wrapped[1])
        endif
      endif
    endif

    " POS blocks
    let l:pbs = get(l:g, 'pos_blocks', [])
    if type(l:pbs) == v:t_list && !empty(l:pbs)
      for l:b in l:pbs
        let l:pos = get(l:b, 'pos', '')
        if empty(l:pos) | continue | endif
        call add(l:lines, '  ' . l:pos . ':')

        let l:defs = get(l:b, 'defs', [])
        if type(l:defs) == v:t_list
          for l:d in l:defs
            let l:wrapped = s:deepl_wrap('- ' . l:d, l:width - 6, 3)
            if !empty(l:wrapped)
              call add(l:lines, '    ' . l:wrapped[0])
              if len(l:wrapped) > 1 | call add(l:lines, '    ' . l:wrapped[1]) | endif
              if len(l:wrapped) > 2 | call add(l:lines, '    ' . l:wrapped[2]) | endif
            endif
          endfor
        endif

        let l:more = get(l:b, 'more', 0)
        if l:more > 0
          call add(l:lines, printf('    … (+%d more)', l:more))
        endif
      endfor
    endif

    " Add etymology at the end if exists
    let l:ety = get(l:g, 'etymology', '')
    if !empty(l:ety)
      " Full line width = l:width (мы уже считаем префикс внутри)
      let l:ety_lines = s:deepl_wrap_pref(l:ety, '  Etymology: ', l:width, 3)
      if !empty(l:ety_lines)
        call extend(l:lines, l:ety_lines)
      endif
    endif

    call add(l:lines, '')
  endif

  call add(l:lines, '')

  " Stats line (mastery)
  call add(l:lines, printf('Mastery: %s %d%%   %d/%d   thresh:%d',
        \ l:bar, l:percent, l:mastered, l:total, l:thresh))

  call add(l:lines, l:sep)

  " Footer (keys must match mappings)
  call add(l:lines, 'Keys: 0,1,2,3,4,5 grade • s show • n skip • q close')

  " Write to trainer buffer without switching windows
  call setbufvar(g:deepl_trainer_bufnr, '&modifiable', 1)

  call setbufline(g:deepl_trainer_bufnr, 1, l:lines)

  " Highlight [all done] in header when fallback
  if l:mode ==# 'fallback'
    call deepl#trainer#apply_mode_hl(g:deepl_trainer_bufnr, 1, 'all done')
  else
    call deepl#trainer#apply_mode_hl(g:deepl_trainer_bufnr, 1, '')
  endif

  let l:new_len = len(l:lines)
  let l:old_len = len(getbufline(g:deepl_trainer_bufnr, 1, '$'))
  if l:old_len > l:new_len
    call deletebufline(g:deepl_trainer_bufnr, l:new_len + 1, '$')
  endif

  let l:winid = bufwinid(g:deepl_trainer_bufnr)
  if l:winid != -1
    call win_execute(
          \ l:winid,
          \ 'call deepl#trainer#apply_hl('
          \ . g:deepl_trainer_bufnr . ', '
          \ . string(l:word) . ', '
          \ . string(l:tr) . ', '
          \ . a:show_translation . ')'
          \ )
  endif

  call setbufvar(g:deepl_trainer_bufnr, '&modifiable', 0)
endfunction
" -------------------------------------------------------
" Normalize whitespace to one-line string
function! s:deepl_one_line(text) abort
  if type(a:text) != v:t_string
    return ''
  endif
  let l:s = substitute(a:text, '\n\+', ' ', 'g')
  let l:s = substitute(l:s, '\s\+', ' ', 'g')
  return trim(l:s)
endfunction
" -------------------------------------------------------
" Simple word wrap: returns up to a:max_lines lines, each within a:width
" Add ellipsis to a line and keep it within display width
function! s:deepl_add_ellipsis(line, width) abort
  let l:s = a:line
  if a:width <= 1
    return '…'
  endif

  " If it already fits with ellipsis, just append
  if strdisplaywidth(l:s . '…') <= a:width
    return l:s . '…'
  endif

  " Otherwise trim chars until it fits
  while strchars(l:s) > 0 && strdisplaywidth(l:s . '…') > a:width
    let l:s = strcharpart(l:s, 0, strchars(l:s) - 1)
  endwhile

  return (empty(l:s) ? '…' : l:s . '…')
endfunction
" -------------------------------------------------------
" Simple word wrap: returns up to a:max_lines lines, each within a:width
function! s:deepl_wrap(text, width, max_lines) abort
  let l:t = s:deepl_one_line(a:text)
  if empty(l:t) || a:width <= 5
    return []
  endif

  let l:words = split(l:t, ' ')
  let l:out = []
  let l:cur = ''

  for l:w in l:words
    if empty(l:cur)
      let l:cur = l:w
    elseif strdisplaywidth(l:cur . ' ' . l:w) <= a:width
      let l:cur .= ' ' . l:w
    else
      call add(l:out, l:cur)

      " Reached max lines -> truncate with ellipsis
      if len(l:out) >= a:max_lines
        let l:out[-1] = s:deepl_add_ellipsis(l:out[-1], a:width)
        return l:out
      endif

      let l:cur = l:w
    endif
  endfor

  if !empty(l:cur) && len(l:out) < a:max_lines
    call add(l:out, l:cur)
  endif

  return l:out
endfunction
" -------------------------------------------------------
" Wrap text with a prefix on the first line and aligned continuation lines.
" Example:
"   prefix="  Etymology: "
"   next lines will start with spaces of same display width (align under 'E')
function! s:deepl_wrap_pref(text, prefix, width, max_lines) abort
  let l:t = s:deepl_one_line(a:text)
  if empty(l:t) || a:width <= 10
    return []
  endif

  let l:pref   = a:prefix
  let l:indent = repeat(' ', strdisplaywidth(l:pref))

  " Available widths for content (first line vs continuation lines)
  let l:w1 = a:width - strdisplaywidth(l:pref)
  let l:wN = a:width - strdisplaywidth(l:indent)
  let l:w1 = max([10, l:w1])
  let l:wN = max([10, l:wN])

  let l:words = split(l:t, ' ')
  let l:out = []
  let l:cur = ''
  let l:line_no = 0

  for l:w in l:words
    let l:lim = (l:line_no == 0 ? l:w1 : l:wN)

    if empty(l:cur)
      let l:cur = l:w
    elseif strdisplaywidth(l:cur . ' ' . l:w) <= l:lim
      let l:cur .= ' ' . l:w
    else
      call add(l:out, (l:line_no == 0 ? l:pref : l:indent) . l:cur)
      let l:line_no += 1

      " If we reached limit, add ellipsis to the last visible line
      if len(l:out) >= a:max_lines
        let l:last = l:out[-1]
        " Ensure ellipsis fits into width
        while strdisplaywidth(l:last . '…') > a:width && strchars(l:last) > 0
          let l:last = strcharpart(l:last, 0, strchars(l:last) - 1)
        endwhile
        let l:out[-1] = l:last . '…'
        return l:out
      endif

      let l:cur = l:w
    endif
  endfor

  if !empty(l:cur) && len(l:out) < a:max_lines
    call add(l:out, (l:line_no == 0 ? l:pref : l:indent) . l:cur)
  endif

  return l:out
endfunction

" -------------------------------------------------------
" Clear previous highlight matches in the trainer window (buffer-local target).
function! s:deepl_trainer_hl_clear(bufnr) abort
  let l:winid = bufwinid(a:bufnr)
  if l:winid == -1
    return
  endif

  let l:old = getwinvar(l:winid, 'deepl_trainer_match_ids', [])
  for l:id in l:old
    if type(l:id) == v:t_number && l:id > 0
      " matchdelete must run in the same window where matchadd was done
      try
        call win_execute(l:winid, 'call matchdelete(' . l:id . ')')
      catch
      endtry
    endif
  endfor

  call setwinvar(l:winid, 'deepl_trainer_match_ids', [])
endfunction

" -------------------------------------------------------
" Ensure highlight groups exist (idempotent)
function! s:deepl_trainer_ensure_hl() abort
  if !hlexists('DeepLTrainerEmph')
    " Bold + green (works in terminal and GUI)
    highlight default DeepLTrainerEmph cterm=bold ctermfg=Green gui=bold guifg=Green
  endif
endfunction

" Highlight only the value after a label on the same line:
"   Unit: <word>   Translation: <tr>
function! s:deepl_trainer_hl_value(bufnr, winid, label, value, group) abort
  if empty(a:value)
    return []
  endif

  let l:lines = getbufline(a:bufnr, 1, '$')
  if empty(l:lines)
    return []
  endif

  " Very nomagic, so we mostly match literal text
  let l:label = escape(a:label, '\')
  let l:val   = escape(a:value, '\')

  " Find the first line that contains the label
  let l:lnum = -1
  for l:i in range(1, len(l:lines))
    if l:lines[l:i - 1] =~# '\V' . l:label
      let l:lnum = l:i
      break
    endif
  endfor
  if l:lnum == -1
    return []
  endif

  " Match only the value (start at \zs), do NOT include the label
  " Example: \%2l\VTranslation:\s\+\zsпредположить
  let l:pat = '\%' . l:lnum . 'l\V' . l:label . '\s\+\zs' . l:val

  let l:id = s:deepl_win_matchadd(a:winid, a:group, l:pat, 20)
  return l:id > 0 ? [l:id] : []
endfunction

" Apply highlighting for the Unit word and Translation value on line 2
function! s:deepl_trainer_apply_hl(bufnr, word, tr, show_translation) abort
  let l:winid = bufwinid(a:bufnr)
  if l:winid == -1
    return
  endif

  " Ensure strings (avoid strlen() errors on v:null)
  let l:word = type(a:word) == v:t_string ? a:word : string(a:word)
  let l:tr   = type(a:tr)   == v:t_string ? a:tr   : string(a:tr)

  call s:deepl_trainer_hl_clear(a:bufnr)

  let l:lines = getbufline(a:bufnr, 1, '$')
  if empty(l:lines)
    return
  endif

  " Collect matchaddpos ids (stored per-window)
  let l:ids = []

  " Highlight only srs_hard (without brackets) inside [srs_hard]
  let l:max = min([len(l:lines), 5])
  for l:i in range(1, l:max)
    let l:s = l:lines[l:i - 1]
    let l:tag_full = '[srs_hard]'
    let l:p = stridx(l:s, l:tag_full)
    if l:p >= 0
      let l:col = l:p + 2                 " 1-based col, points to 's' after '['
      let l:len = strlen('srs_hard')
      let l:id = s:deepl_win_matchaddpos(l:winid, 'DeepLTrainerModeHard', [[l:i, l:col, l:len]], 10)
      if l:id > 0
        call add(l:ids, l:id)
      endif
      break
    endif
  endfor

  " Highlight Unit value only (after 'Unit: ')
  call extend(l:ids, s:deepl_trainer_hl_value(a:bufnr, l:winid, 'Unit:', l:word, 'DeepLTrainerUnitWord'))

  " Highlight Translation value only (after 'Translation: ')
  if a:show_translation
    call extend(l:ids, s:deepl_trainer_hl_value(a:bufnr, l:winid, 'Translation:', l:tr, 'DeepLTrainerTranslationWord'))
  endif

  " Highlight word inside Context line(s) (same color as Unit word, no bold)
  if !empty(l:word)
    call extend(l:ids, s:deepl_trainer_hl_in_context(a:bufnr, l:winid, l:word, 'DeepLTrainerContextWord'))
  endif

  " Drop failed match ids (0/negative)
  let l:ids = filter(l:ids, 'type(v:val) == v:t_number && v:val > 0')
  
  " Save ids so we can clear them next render
  call setwinvar(l:winid, 'deepl_trainer_match_ids', l:ids)
endfunction
" -------------------------------------------------------
function! deepl#translation_apply_hl(bufnr) abort
  " Must run in translation window context (call via win_execute()).
  if a:bufnr <= 0
    return
  endif
  if bufnr('%') != a:bufnr
    execute 'silent! buffer ' . a:bufnr
  endif

  " Ensure highlight groups exist (colorscheme may reset links)
  if !hlexists('DeepLTransHeader')
    highlight link DeepLTransHeader Identifier
  else
    highlight link DeepLTransHeader Identifier
  endif
  if !hlexists('DeepLTransSRC')
    highlight link DeepLTransSRC Comment
  else
    highlight link DeepLTransSRC Comment
  endif
  if !hlexists('DeepLTransTRN')
    highlight link DeepLTransTRN String
  else
    highlight link DeepLTransTRN String
  endif

  " Clear old window-local matches
  if exists('w:deepl_trans_match_ids')
    for l:id in w:deepl_trans_match_ids
      silent! call matchdelete(l:id)
    endfor
  endif
  let w:deepl_trans_match_ids = []

  " Header like: #2 [EN -> RU]
  call add(w:deepl_trans_match_ids, matchadd('DeepLTransHeader', '^#\d\+.*$'))
  " SRC/TRN lines
  call add(w:deepl_trans_match_ids, matchadd('DeepLTransSRC', '^SRC:.*$'))
  call add(w:deepl_trans_match_ids, matchadd('DeepLTransTRN', '^TRN:.*$'))
endfunction
" -------------------------------------------------------
function! s:deepl_trainer_hl_in_context(bufnr, winid, word, group) abort
  if empty(a:word)
    return []
  endif

  let l:lines = getbufline(a:bufnr, 1, '$')
  if len(l:lines) < 3
    return []
  endif

  " Найдём строку, которая начинается с "Context:"
  let l:ctx_lnum = -1
  for l:i in range(1, len(l:lines))
    if l:lines[l:i - 1] =~# '^CTX:'
      let l:ctx_lnum = l:i
      break
    endif
  endfor
  if l:ctx_lnum == -1
    return []
  endif

  " Escape regex chars in word
  let l:w = escape(a:word, '\.^$~[]*\/')
  " Highlight only whole words on the context line (case-insensitive можно убрать \c)
  let l:pat = '\%'.l:ctx_lnum.'l\<'.l:w.'\>'

  let l:id = s:deepl_win_matchadd(a:winid, a:group, l:pat, 10)
  return l:id > 0 ? [l:id] : []
endfunction

" Add a match inside a specific window and return its match id.
function! s:deepl_win_matchadd(winid, group, pattern, priority) abort
  if a:winid == -1
    return 0
  endif

  " Store the id in a window variable to fetch it back reliably.
  let l:cmd = printf(
        \ "let w:deepl_trainer_tmp_mid = matchadd(%s, %s, %d)",
        \ string(a:group),
        \ string(a:pattern),
        \ a:priority
        \ )
  call win_execute(a:winid, l:cmd)

  let l:id = getwinvar(a:winid, 'deepl_trainer_tmp_mid', 0)
  call win_execute(a:winid, 'unlet! w:deepl_trainer_tmp_mid')
  return l:id
endfunction

" Add a matchaddpos inside a specific window and return its match id.
function! s:deepl_win_matchaddpos(winid, group, poslist, priority) abort
  if a:winid == -1
    return 0
  endif

  let l:cmd = printf(
        \ "let w:deepl_trainer_tmp_mid = matchaddpos(%s, %s, %d)",
        \ string(a:group),
        \ string(a:poslist),
        \ a:priority
        \ )
  call win_execute(a:winid, l:cmd)

  let l:id = getwinvar(a:winid, 'deepl_trainer_tmp_mid', 0)
  call win_execute(a:winid, 'unlet! w:deepl_trainer_tmp_mid')
  return l:id
endfunction

" -------------------------------------------------------
" Functions: Start, Next, Show, DeepLTrainerMarkHard, DeepLTrainerShow

function! DeepLTrainerNext() abort
  if g:deepl_trainer_bufnr <= 0 || !bufexists(g:deepl_trainer_bufnr)
    echo "Trainer window is not open. Use :DeepLTrainerStart"
    return
  endif

  " Determine source filter (EN/DA)
  if !exists('g:deepl_word_src_lang') || empty(g:deepl_word_src_lang)
    let l:src_filter = 'EN'
  else
    let l:src_filter = g:deepl_word_src_lang
  endif

  " Exclude the currently shown card so 'n' never repeats it in this session
  let l:cur_id = get(g:deepl_trainer_current, 'card_id', 0)
  call s:deepl_exclude_add(l:cur_id)

  if !exists('g:deepl_trainer_exclude') || type(g:deepl_trainer_exclude) != v:t_list
    let g:deepl_trainer_exclude = []
  endif

  if g:deepl_backend ==# 'http'
    " HTTP backend: /train/next
    let l:payload = json_encode({
          \ 'src_filter': l:src_filter,
          \ 'exclude_card_ids': g:deepl_trainer_exclude,
          \ })

    let l:cmd = [
          \ 'curl', '-sS',
          \ '-X', 'POST',
          \ '-H', 'Content-Type: application/json',
          \ '-d', l:payload,
          \ g:deepl_api_base . '/train/next',
          \ ]
  else
    " Local python backend: use trainer_cmd (next)
    let l:cmd = [
          \ 'python3',
          \ '-m', 'vim_deepl.tools.trainer_cmd',
          \ 'next',
          \ '--src', l:src_filter,
          \ ]
  endif

  let g:deepl_trainer_last_op = 'next'

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

" Add card_id to the exclude list (unique), keep last N items
function! s:deepl_exclude_add(card_id) abort
  if a:card_id <= 0
    return
  endif

  if !exists('g:deepl_trainer_exclude') || type(g:deepl_trainer_exclude) != v:t_list
    let g:deepl_trainer_exclude = []
  endif

  " Deduplicate
  let l:i = index(g:deepl_trainer_exclude, a:card_id)
  if l:i >= 0
    call remove(g:deepl_trainer_exclude, l:i)
  endif

  call add(g:deepl_trainer_exclude, a:card_id)

  " Cap list size (keep last 200)
  let l:max = 200
  if len(g:deepl_trainer_exclude) > l:max
    call remove(g:deepl_trainer_exclude, 0, len(g:deepl_trainer_exclude) - l:max - 1)
  endif
endfunction

function! DeepLTrainerSkip() abort
  if empty(g:deepl_trainer_current)
    return
  endif

  let l:cid = get(g:deepl_trainer_current, 'card_id', 0)
  if l:cid > 0
    " Add current card to exclude list for this session
    if index(g:deepl_trainer_exclude, l:cid) < 0
      call add(g:deepl_trainer_exclude, l:cid)
    endif
  endif

  call DeepLTrainerNext()
endfunction

function! DeepLTrainerIgnore() abort
  if empty(g:deepl_trainer_current)
    return
  endif

  let l:word = get(g:deepl_trainer_current, 'term', get(g:deepl_trainer_current, 'word', '')) 
  let l:entry_id = get(g:deepl_trainer_current, 'entry_id', 0)
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
    let l:payload = json_encode({'entry_id': l:entry_id, 'word': l:word, 'src_filter': l:src}) 
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
  echo 'Ignored: ' . l:word
endfunction

" Review current card with a grade (0..5) and load next item
function! DeepLTrainerReview(grade) abort
  if g:deepl_trainer_bufnr <= 0 || !bufexists(g:deepl_trainer_bufnr)
    echo "Trainer window is not open. Use :DeepLTrainerStart"
    return
  endif

  if empty(g:deepl_trainer_current)
    echo "Trainer: no active item"
    return
  endif

  let l:card_id = get(g:deepl_trainer_current, 'card_id', 0)
  if l:card_id <= 0
    echo "Trainer: current item has no card_id"
    return
  endif

  " Use current source language toggle (EN/DA)
  if !exists('g:deepl_word_src_lang') || empty(g:deepl_word_src_lang)
    let l:src_filter = 'EN'
  else
    let l:src_filter = g:deepl_word_src_lang
  endif

  if g:deepl_backend ==# 'http'
    " HTTP backend: /train/review
    
    " Exclude the just-reviewed card from further :train/next picks in this session
    call s:deepl_exclude_add(l:card_id)

    let l:payload = json_encode({'src_filter': l:src_filter, 'card_id': l:card_id, 'grade': a:grade})
    let l:cmd = [
          \ 'curl', '-sS',
          \ '-X', 'POST',
          \ '-H', 'Content-Type: application/json',
          \ '-d', l:payload,
          \ g:deepl_api_base . '/train/review',
          \ ]
  else
    " Local python backend: trainer_cmd (review -> returns next item)
    let l:cmd = [
          \ 'python3',
          \ '-m', 'vim_deepl.tools.trainer_cmd',
          \ 'review',
          \ '--src', l:src_filter,
          \ '--card-id', string(l:card_id),
          \ '--grade', string(a:grade),
          \ ]
  endif

  let g:deepl_trainer_last_op = 'review'
  let g:deepl_trainer_last_reviewed_id = get(g:deepl_trainer_current, 'card_id', 0)
 
  let g:deepl_trainer_last_card = l:card_id
  call s:deepl_exclude_add(l:card_id)


  call job_start(l:cmd, {
        \ 'out_cb': function('s:DeepLTrainOut'),
        \ 'err_cb': function('s:DeepLTrainErr'),
        \ 'exit_cb': function('s:DeepLTrainExit'),
        \ 'out_mode': 'raw',
        \ 'err_mode': 'raw',
        \ })
endfunction


function! DeepLTrainerShow() abort
  if empty(g:deepl_trainer_current)
    return
  endif
  " 1 = show translation
  call DeepLTrainerRender(1)
endfunction

function! deepl#trainer_start() abort
  " Reset per-session exclude list
  let g:deepl_trainer_exclude = []

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

  let l:bufname = '__DeepL_Trainer__'
  let l:bn = bufnr(l:bufname)

  " Prefer Study UI trainer window if available
  if exists('*deepl#ui#ensure') && exists('*deepl#ui#trainer_winid')
    call deepl#ui#ensure()
    let l:tw = deepl#ui#trainer_winid()

    if l:tw
      call win_gotoid(l:tw)

      if l:bn != -1
        execute 'silent! buffer ' . l:bn
      else
        enew
        execute 'silent! file ' . l:bufname
      endif

      let g:deepl_trainer_bufnr = bufnr('%')
    else
      " Fallback to legacy split if UI exists but trainer window is not found
      let l:tw = 0
    endif
  else
    let l:tw = 0
  endif

  " Legacy mode: open trainer window at the bottom with fixed height 8
  if !l:tw
    if l:bn != -1
      let l:wnr = bufwinnr(l:bn)
      if l:wnr != -1
        execute l:wnr . 'wincmd w'
      else
        botright 8split
        execute 'buffer ' . l:bn
      endif
    else
      botright 8split __DeepL_Trainer__
    endif
    let g:deepl_trainer_bufnr = bufnr('%')
    setlocal winfixheight
  endif

  " Common buffer/window options
  setlocal buftype=nofile bufhidden=wipe noswapfile nobuflisted
  setlocal wrap linebreak
  setlocal nonumber norelativenumber

  " In UI mode we do not force height here; UI reflow controls it
  " In legacy mode winfixheight is set above

  setlocal modifiable

  " Local key mappings in trainer buffer
  nnoremap <silent> <buffer> q :DeepLStudyClose<CR>
  nnoremap <silent> <buffer> n :call DeepLTrainerSkip()<CR>
  nnoremap <silent> <buffer> s :call DeepLTrainerShow()<CR>
  "nnoremap <silent> <buffer> x :call DeepLTrainerMarkHard()<CR>
  "nnoremap <silent> <buffer> d :call DeepLTrainerIgnore()<CR>

  " Grades 0..5 (SRS review)
  nnoremap <silent> <buffer> 0 :call DeepLTrainerReview(0)<CR>
  nnoremap <silent> <buffer> 1 :call DeepLTrainerReview(1)<CR>
  nnoremap <silent> <buffer> 2 :call DeepLTrainerReview(2)<CR>
  nnoremap <silent> <buffer> 3 :call DeepLTrainerReview(3)<CR>
  nnoremap <silent> <buffer> 4 :call DeepLTrainerReview(4)<CR>
  nnoremap <silent> <buffer> 5 :call DeepLTrainerReview(5)<CR>

  " Quick 'again' (alias for grade 0)
  "nnoremap <silent> <buffer> a :call DeepLTrainerReview(0)<CR>

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
" Extract a short “sentence-like” context around the cursor from the current line:
" find nearest punctuation boundaries (. ! ? : ;) left/right of the cursor, trim, and cap length (used as DeepL context).

function! s:deepl_sentence_context() abort
  " 1) Take paragraph (between blank lines) instead of a single line
  let l:start = search('^\s*$', 'bnW') + 1
  if l:start <= 0 | let l:start = 1 | endif

  let l:end = search('^\s*$', 'nW') - 1
  if l:end <= 0 | let l:end = line('$') | endif
  if l:end < l:start | let l:end = l:start | endif

  let l:lines = getline(l:start, l:end)
  if empty(l:lines)
    return ''
  endif

  " 2) Build a single text line from the paragraph, keep a raw version to compute offsets
  let l:text_raw = substitute(join(l:lines, ' '), '\s\+', ' ', 'g')

  " 3) Compute cursor index inside the joined paragraph
  let l:cur_lnum = line('.')
  let l:cur_col0 = col('.') - 1
  if l:cur_col0 < 0 | let l:cur_col0 = 0 | endif

  " Clamp col to the current line length
  let l:cur_line = getline(l:cur_lnum)
  if l:cur_col0 > strlen(l:cur_line)
    let l:cur_col0 = strlen(l:cur_line)
  endif

  " Sum lengths of lines before current one (+1 for the join-space)
  let l:idx = 0
  let l:before = l:cur_lnum - l:start
  if l:before > 0
    for l:i in range(0, l:before - 1)
      let l:idx += strlen(l:lines[l:i]) + 1
    endfor
  endif
  let l:idx += l:cur_col0

  " 4) Trim (may shift idx if paragraph starts with spaces)
  let l:lead = strlen(matchstr(l:text_raw, '^\s*'))
  let l:text = trim(l:text_raw)
  let l:idx -= l:lead
  if l:idx < 0 | let l:idx = 0 | endif
  if l:idx > strlen(l:text) | let l:idx = strlen(l:text) | endif

  if empty(l:text)
    return ''
  endif

  " 5) Find sentence boundaries around idx
  let l:prefix = strpart(l:text, 0, l:idx)

  let l:left_dot = strridx(l:prefix, '.')
  let l:left_exc = strridx(l:prefix, '!')
  let l:left_q   = strridx(l:prefix, '?')
  let l:left_col = strridx(l:prefix, ':')
  let l:left_sem = strridx(l:prefix, ';')

  let l:sent_start = max([l:left_dot, l:left_exc, l:left_q, l:left_col, l:left_sem]) + 1

  " Sentence boundaries (right side) using match() patterns.
  let l:sent_end = -1
  for l:pat in ['\V.', '\V!', '\V?', '\V:', '\V;']
    let l:p = match(l:text, l:pat, l:idx)
    if l:p != -1 && (l:sent_end == -1 || l:p < l:sent_end)
      let l:sent_end = l:p
    endif
  endfor

  if l:sent_end == -1
    let l:sent = strpart(l:text, l:sent_start)
  else
    let l:sent = strpart(l:text, l:sent_start, (l:sent_end - l:sent_start + 1))
  endif

  let l:sent = trim(l:sent)

  " Limit for DeepL context
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

  " Build the right-side tag: SRC + optional CTX.
  let l:ctx_used = get(l:resp, 'context_used', v:false)
  let l:from_cache = get(l:resp, 'from_cache', v:false)

  " Source label:
  " - from_cache=true  -> Dictionary (SQLite)
  " - from_cache=false -> DeepL API (fresh request)
  let l:src_label = l:from_cache ? 'Dictionary' : 'DeepL API'

  " Right-side tag: 'SRC: ...' + optional ' | CTX'
  let l:src_tag = 'SRC: ' . l:src_label
  if l:ctx_used
    let l:src_tag .= ' | CTX'
  endif

  " Build popup lines via MW popup module (raw_json is stored in SQLite).
  let l:mw = l:resp.mw_definitions
  let g:deepl_mw_audio_ids = get(l:mw, 'audio_ids', [])
  let g:deepl_mw_audio_idx = 0

  let l:width = get(g:, 'deepl_mw_popup_width', 80)

  let l:alts = get(l:resp, 'ctx_translations', [])
  if type(l:alts) != v:t_list
    let l:alts = []
  endif
  
  let l:lines = deepl#mw_popup#build_lines(
        \ l:source,
        \ l:translation,
        \ get(l:mw, 'raw_json', ''),
        \ l:width,
        \ l:src_tag,
        \ l:alts,
        \ ) 
  
  " Title is used as a compact header line for SRC/CTX.
  let l:title = '-  MW  -  ' . l:src_tag. ' '
  call s:deepl_show_defs_buffer(l:lines, l:title)

endfunction

function! deepl#debug_sentence_context() abort
  return s:deepl_sentence_context()
endfunction

" -------------------------------------------------------
" Internal helper: show list of lines in a popup or preview window
function! s:deepl_show_defs_buffer(lines, title) abort
  if has('popupwin')
    let width  = get(g:, 'deepl_mw_popup_width', 80)

    let maxh = get(g:, 'deepl_mw_popup_maxheight', 21)
    let height = len(a:lines) > maxh ? maxh : len(a:lines)
    let height = height + 1

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
          \ 'scrollbar': 1,
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
    " MW popup highlighting (autoloaded).
    try
      call deepl#mw_hl#apply(l:popup_id)
    catch
      echohl ErrorMsg
      echom 'MW hl error: ' . v:exception
      echohl None
    endtry
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
" Build a structured MW popup layout (context + header + POS sections).
function! s:deepl_build_mw_popup_lines(term, translation, ctx_text, mw) abort
  " All comments in this function are in English (as requested).

  let l:lines = []

  " 1) Context (shown first)
  if type(a:ctx_text) == v:t_string && !empty(a:ctx_text)
    call add(l:lines, 'CTX: ' . a:ctx_text)
    call add(l:lines, '')
  endif

  " 2) Main title line (word -> translation)
  if type(a:translation) == v:t_string && !empty(a:translation)
    call add(l:lines, a:term . ' -> ' . a:translation)
  else
    call add(l:lines, a:term)
  endif

  " 3) Header info from parsed MW (optional)
  let l:info = get(a:mw, 'info', {})
  if type(l:info) == v:t_dict && !empty(l:info)
    let l:hw   = get(l:info, 'headword', '')
    let l:pron = get(l:info, 'pronunciation', '')
    let l:pos  = get(l:info, 'main_pos', '')
    let l:stems = get(l:info, 'stems', [])
    let l:has_audio = get(l:info, 'has_audio', v:false)

    if !empty(l:hw)   | call add(l:lines, 'HW: ' . l:hw) | endif
    if !empty(l:pron) | call add(l:lines, 'PRON: ' . l:pron) | endif
    if !empty(l:pos)  | call add(l:lines, 'POS: ' . l:pos) | endif

    if type(l:stems) == v:t_list && !empty(l:stems)
      " Show stems compactly; popup wrapping is enabled anyway.
      call add(l:lines, 'STEMS: ' . join(l:stems[:15], ', '))
    endif

    call add(l:lines, 'AUDIO: ' . (l:has_audio ? 'yes (F2)' : 'no'))
    call add(l:lines, '')
  endif

  " 4) Suggestions mode (MW returned a list of strings)
  if get(a:mw, 'status', '') ==# 'suggestions'
    let l:sugg = get(a:mw, 'suggestions', [])
    if type(l:sugg) == v:t_list && !empty(l:sugg)
      call add(l:lines, 'SUGGESTIONS:')
      for s in l:sugg
        if type(s) == v:t_string && !empty(s)
          call add(l:lines, '  • ' . s)
        endif
      endfor
    else
      call add(l:lines, '(no suggestions)')
    endif
    return l:lines
  endif

  " 5) Definitions by part of speech (stable order)
  let l:order = [
        \ ['noun', 'NOUN'],
        \ ['verb', 'VERB'],
        \ ['adjective', 'ADJECTIVE'],
        \ ['adverb', 'ADVERB'],
        \ ['other', 'OTHER'],
        \ ]

  let l:any_defs = 0

  for [l:key, l:label] in l:order
    let l:defs = get(a:mw, l:key, [])
    if type(l:defs) == v:t_list && !empty(l:defs)
      let l:any_defs = 1
      call add(l:lines, l:label . ':')
      for d in l:defs
        if type(d) == v:t_string && !empty(d)
          call add(l:lines, '  • ' . d)
        endif
      endfor
      call add(l:lines, '')
    endif
  endfor

  if !l:any_defs
    call add(l:lines, '(no MW definitions)')
  endif

  " Trim trailing blank lines
  while !empty(l:lines) && l:lines[-1] ==# ''
    call remove(l:lines, -1)
  endwhile

  return l:lines
endfunction

" -------------------------------------------------------
" Run a shell command in background without blocking Vim.
function! s:run_bg(cmd) abort
  if exists('*job_start')
    " Vim job control (preferred).
    call job_start(['sh', '-lc', a:cmd], {'out_io': 'null', 'err_io': 'null'})
  else
    " Fallback for very old Vim.
    call system(a:cmd . ' >/dev/null 2>&1 &')
  endif
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
" Trainer highlight groups
if !exists('g:deepl_trainer_hl_defined')
  let g:deepl_trainer_hl_defined = 1

  " Unit word (value only)
  highlight default DeepLTrainerUnitWord cterm=bold ctermfg=121 gui=bold

  " Translation value only
  highlight default DeepLTrainerTranslationWord cterm=bold ctermfg=221 gui=bold

  " Mode tag, e.g. [srs_hard]
  highlight default DeepLTrainerModeHard cterm=bold ctermfg=94 gui=bold

  highlight default DeepLTrainerContextWord ctermfg=121 gui=NONE cterm=NONE

endif
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

  " Play MW audio (F4).
  if a:key ==# "\<F4>"
    if get(g:, 'deepl_backend', '') !=# 'http'
      echo "MW audio requires g:deepl_backend='http'"
      return ''
    endif

    let g:deepl_mw_audio_idx = deepl#mw_audio#handle_f4(
          \ function('s:http_post_json'),
          \ get(g:, 'deepl_api_base', ''),
          \ get(g:, 'deepl_mw_audio_ids', []),
          \ get(g:, 'deepl_mw_audio_idx', 0)
          \ )
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
  " BRANCH 2: 4+ words -> UI window if available, fallback to history window
  " -------------------------------
  if exists('*deepl#ui#ensure')
    call deepl#ui#ensure()
  endif

  " Use history appending logic to avoid wiping previous entries
  call DeepLShowInWindow()

endfunction

function! deepl#translate_from_visual() abort
  call deepl#ui#ensure()

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
    
    let g:deepl_last_sel_src = l:text
    let g:deepl_pending_sel = ''

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

  " UI-first path: reuse the right panel translation window if available
  if exists('*deepl#ui#ensure')
    let l:bufname = '__DeepL_Translation__'
    let l:bufnr_ui = bufnr(l:bufname)

    " Try to find an existing window showing the translation buffer
    let l:wnr_ui = (l:bufnr_ui == -1) ? -1 : bufwinnr(l:bufnr_ui)

    " If not visible, open the study UI once and try again
    if l:wnr_ui == -1
      call deepl#ui#ensure()
      let l:bufnr_ui = bufnr(l:bufname)
      let l:wnr_ui = (l:bufnr_ui == -1) ? -1 : bufwinnr(l:bufnr_ui)
    endif

    " If still not visible, fall back to legacy behavior
    if l:wnr_ui != -1
      let l:curid = win_getid()
      execute l:wnr_ui . 'wincmd w'

      " Trim trailing newlines to avoid empty lines at the end
      let l:entry = substitute(g:deepl_last_entry, '\n\+$', '', '')
      let l:lines = split(l:entry, "\n")

      setlocal buftype=nofile bufhidden=hide nobuflisted noswapfile
      setlocal wrap linebreak
      setlocal nonumber norelativenumber

      " Buffer-local mapping:
      " q - clear history/output but keep the UI layout
      nnoremap <silent> <buffer> q :call DeepLClearHistory()<CR>

      setlocal modifiable

      " Add a separator only when needed (avoid double blank lines)
      if !(line('$') == 1 && getline(1) ==# '')
        if getline(line('$')) !=# ''
          call append(line('$'), '')
        endif
      endif

      if line('$') == 1 && getline(1) ==# ''
        call setline(1, l:lines)
      else
        call append(line('$'), l:lines)
      endif

      setlocal nomodifiable

      " Keep cursor at the very bottom (latest line) and show the bottom of the buffer
      normal! G
      normal! zb

      call win_gotoid(l:curid)
      silent! echo ""
      silent! redraw!
      return
    endif
  endif

  " Fallback path: legacy bottom split behavior (unchanged layout logic)
  let l:curwin  = winnr()

  let l:lines   = split(g:deepl_last_entry, "\n")
  let l:bufname = "__DeepL_Translation__"
  let l:bufnr   = bufnr(l:bufname)

  let l:win_height = 5

  if l:bufnr == -1
    execute 'botright ' . l:win_height . 'new'
    let l:bufnr = bufnr('%')
    silent! execute 'file ' . l:bufname
  else
    let l:wnr = bufwinnr(l:bufnr)
    if l:wnr == -1
      execute 'botright ' . l:win_height . 'split'
      silent! execute 'buffer ' . l:bufnr
    else
      execute l:wnr . 'wincmd w'
      execute 'resize ' . l:win_height
    endif
  endif

  setlocal buftype=nofile bufhidden=hide nobuflisted noswapfile
  setlocal wrap linebreak
  setlocal nonumber norelativenumber
  setlocal winfixheight

  nnoremap <silent> <buffer> q :call DeepLClearHistory()<CR>

  setlocal modifiable

  " Add a separator only when needed (avoid double blank lines)
  if !(line('$') == 1 && getline(1) ==# '')
    if getline(line('$')) !=# ''
      call append(line('$'), '')
    endif
  endif

  if line('$') == 1 && getline(1) ==# ''
    call setline(1, l:lines)
  else
    call append(line('$'), l:lines)
  endif

  setlocal nomodifiable

  " Re-apply translation highlights in this window
  let l:bn = bufnr('%')
  call win_execute(l:winid, 'call deepl#translation_apply_hl(' . l:bn . ')')

" setlocal modifiable
 " if line('$') == 1 && getline(1) ==# ''
 "   call setline(1, l:lines)
 " else
 "   call append(line('$'), '')
 "   call append(line('$'), l:lines)
 " endif
 " setlocal nomodifiable

  normal! G
  let l:header_lnum = search('^#\d\+ ', 'bW')
  if l:header_lnum > 0
    let l:entry_len = line('$') - l:header_lnum + 1
    if l:entry_len > (winheight(0) - 1)
      normal! zb
    else
      call cursor(l:header_lnum, 1)
      normal! zt
    endif
  else
    normal! zb
  endif

  if l:curwin > 0 && winnr() != l:curwin
    execute l:curwin . 'wincmd w'
  endif

  silent! echo ""
  silent! redraw!
endfunction

function! DeepLClearHistory() abort
  let l:bufname = '__DeepL_Translation__'
  let l:bufnr   = bufnr(l:bufname)

  " Reset history counters/state
  let g:deepl_request_counter = 0
  let g:deepl_last_entry = ''

  " UI mode: keep the right panel window/buffer, just clear content
  if exists('*deepl#ui#translation_winid') && exists('*deepl#ui#ensure')
    call deepl#ui#ensure()
    let l:winid = deepl#ui#translation_winid()
    if l:winid
      let l:curid = win_getid()
      call win_gotoid(l:winid)

      setlocal modifiable
      silent! %delete _
      setlocal nomodifiable

      call win_gotoid(l:curid)
      echo 'DeepL history cleared'
      return
    endif
  endif

  " Legacy mode: if no buffer exists, nothing else to do
  if l:bufnr == -1
    echo 'DeepL history cleared'
    return
  endif

  " If buffer is visible, close it (legacy bottom split behavior)
  let l:wnr = bufwinnr(l:bufnr)
  if l:wnr != -1
    execute l:wnr . 'wincmd w'
    execute 'bwipeout!'
    echo 'DeepL history cleared'
    return
  endif

  " Buffer exists but is hidden: clear its content
  call setbufline(l:bufnr, 1, [''])
  call deletebufline(l:bufnr, 2, '$')

  echo 'DeepL history cleared'
endfunction

" Popup highlight groups (MW/word popup header tags)
highlight default DeeplPopupSrcDict cterm=bold ctermfg=108 gui=bold
highlight default DeeplPopupSrcApi  cterm=bold ctermfg=110 gui=bold
highlight default DeeplPopupCtx     cterm=bold ctermfg=179 gui=bold
highlight default DeeplPopupHeaderLeft cterm=bold ctermfg=220 gui=bold

"===========================================================
