" Render trainer UI into the current buffer (__DeepL_Trainer__).
function! deepl#trainer#render(state) abort
  let l:res = get(a:state, 'res', {})
  if empty(l:res)
    return
  endif

  let l:word  = get(l:res, 'term', get(l:res, 'word', ''))
  let l:lang  = get(a:state, 'lang', get(l:res, 'dst_lang', 'RU'))
  let l:ctx   = get(l:res, 'context_raw', '')
  let l:tr    = get(l:res, 'translation', '')
  let l:shown = get(a:state, 'show_translation', 0)

  let l:filter = get(a:state, 'filter', get(l:res, 'src_lang', 'EN'))
  let l:mode_suffix = get(a:state, 'mode_suffix', '')

  let l:today_done  = get(l:res, 'today_done', 0)
  let l:streak_days = get(l:res, 'streak_days', 0)

  let l:stats    = get(l:res, 'stats', {})
  let l:total    = get(l:stats, 'total', 0)
  let l:mastered = get(l:stats, 'mastered', 0)
  let l:thresh   = get(l:stats, 'mastery_threshold', 0)
  let l:percent  = get(l:stats, 'mastery_percent', 0)

  " Determine width
  let l:width = max([40, winwidth(0) - 1])
  let l:sep = repeat('-', l:width)

  " Progress bar
  let l:bar_w = max([10, min([24, l:width - 26])])
  let l:fill = float2nr(l:bar_w * (l:percent / 100.0))
  let l:fill = max([0, min([l:bar_w, l:fill])])
  let l:bar = '[' . repeat('#', l:fill) . repeat('-', l:bar_w - l:fill) . ']'

  " Context sanity
  if type(l:ctx) != v:t_string
    let l:ctx = ''
  endif
  if l:ctx !~# '\s' && l:ctx !~# '[\.\!\?,;:]'
    let l:ctx = ''
  endif

  let l:lines = []

  " Header
  call add(l:lines, printf('DL Trainer (%s -> %s)%s Reviewed: %d Run: %dd',
        \ l:filter, l:lang, l:mode_suffix, l:today_done, l:streak_days))

  " SRS details
  let l:reps   = get(l:res, 'reps', 0)
  let l:lapses = get(l:res, 'lapses', 0)
  let l:wrong  = get(l:res, 'wrong_streak', 0)

  let l:due_raw = get(l:res, 'due_at', '')
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

  " Card block: UNIT + TRN on the same line
  if l:shown
    call add(l:lines, 'UNIT: ' . l:word . '    TRN:  ' . l:tr)
  else
    call add(l:lines, "UNIT: " . l:word . "    TRN:  ???   (press \"s\" to show)")
  endif

  let l:ctx_lines = deepl#trainer#wrap(l:ctx, l:width - 6)
  if !empty(l:ctx_lines)
    call add(l:lines, 'CTX:  ' . get(l:ctx_lines, 0, ''))
    if len(l:ctx_lines) > 1
      call add(l:lines, '      ' . get(l:ctx_lines, 1, ''))
    endif
  endif

  call add(l:lines, '')
  call add(l:lines, printf('Mastery: %s %d%%   %d/%d   thresh:%d',
        \ l:bar, l:percent, l:mastered, l:total, l:thresh))

  call add(l:lines, l:sep)
  call add(l:lines, 'Keys: 0,1,2,3,4,5 grade • s show • n skip • q close')

  " Write buffer (we are in trainer window context via win_execute)
  setlocal modifiable
  silent! %delete _
  call setline(1, l:lines)
  setlocal nomodifiable

  " Apply highlights (UNIT / TRN / context word)
  if exists('*deepl#hl#apply_trainer')
    call deepl#hl#apply_trainer(bufnr('%'), l:unit, l:trn, l:shown)
  elseif exists('*deepl#trainer_apply_hl')
    call deepl#trainer_apply_hl(bufnr('%'), l:unit, l:trn, l:shown)
  endif

endfunction


" Wrap text to width, return list of lines (simple word wrap).
function! deepl#trainer#wrap(text, width) abort
  let l:words = split(substitute(a:text, '\s\+', ' ', 'g'), ' ')
  let l:out = []
  let l:cur = ''

  for l:w in l:words
    if empty(l:cur)
      let l:cur = l:w
    elseif strdisplaywidth(l:cur . ' ' . l:w) <= a:width
      let l:cur .= ' ' . l:w
    else
      call add(l:out, l:cur)
      let l:cur = l:w
    endif
  endfor

  if !empty(l:cur)
    call add(l:out, l:cur)
  endif

  return l:out
endfunction

