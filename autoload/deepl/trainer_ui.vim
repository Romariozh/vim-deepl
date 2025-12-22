" Render trainer UI into the current buffer (__DeepL_Trainer__).
function! deepl#trainer#render(state) abort
  let l:width = winwidth(0)
  let l:sep = repeat('-', max([10, l:width - 1]))

  " Header line (keep it short)
  let l:hdr = printf('DeepL Trainer %s  Today:%s  Streak:%s  Due:%s  Hard:%s',
        \ get(a:state, 'lang_tag', ''),
        \ get(a:state, 'today', 0),
        \ get(a:state, 'streak', '0d'),
        \ get(a:state, 'due', 0),
        \ get(a:state, 'hard', 0))

  let l:unit = get(a:state, 'unit', '')
  let l:ctx  = get(a:state, 'context', '')
  let l:trn  = get(a:state, 'translation', '')
  let l:shown = get(a:state, 'translation_shown', 0)

  let l:lines = []
  call add(l:lines, l:hdr)
  call add(l:lines, l:sep)

  call add(l:lines, 'UNIT: ' . l:unit)

  " Context: max 2 lines, trimmed to window width
  if !empty(l:ctx)
    let l:ctx_lines = deepl#trainer#wrap(l:ctx, l:width - 6)
    call add(l:lines, 'CTX:  ' . get(l:ctx_lines, 0, ''))
    if len(l:ctx_lines) > 1
      call add(l:lines, '      ' . get(l:ctx_lines, 1, ''))
    endif
  else
    call add(l:lines, 'CTX:  ')
  endif

  if l:shown
    call add(l:lines, 'TRN:  ' . l:trn)
  else
    call add(l:lines, "TRN:  [hidden]  press 's' to show")
  endif

  call add(l:lines, l:sep)
  call add(l:lines, "Keys: 0-5 grade   s show   n skip   x hard   d ignore   q close")

  " Write buffer
  setlocal modifiable
  silent! %delete _
  call setline(1, l:lines)
  setlocal nomodifiable

  " Keep cursor at bottom (no jumps around)
  normal! G
  normal! zb
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

