" autoload/deepl/ui.vim

let s:trainer_bufname = '__DeepL_Trainer__'
let s:trans_bufname   = '__DeepL_Translation__'

function! s:find_win_by_bufname(name) abort
  for w in range(1, winnr('$'))
    let b = winbufnr(w)
    if b > 0 && bufname(b) ==# a:name
      return win_getid(w)
    endif
  endfor
  return 0
endfunction

function! s:setup_tool_window() abort
  setlocal buftype=nofile bufhidden=hide noswapfile nobuflisted
  setlocal nonumber norelativenumber signcolumn=no foldcolumn=0
  setlocal nocursorline
endfunction

function! s:setup_trainer_window() abort
  call s:setup_tool_window()
    " Trainer window should wrap long lines
  setlocal wrap
  setlocal linebreak
  setlocal breakindent
endfunction

function! s:setup_translation_window() abort
  call s:setup_tool_window()
  setlocal wrap linebreak
    " Apply window-local highlights for translation buffer
  if exists('*deepl#translation_apply_hl')
    call deepl#translation_apply_hl(bufnr('%'))
  endif
endfunction

function! deepl#ui#ensure() abort
  let l:cur = win_getid()

  let l:tw = s:find_win_by_bufname(s:trainer_bufname)
  let l:sw = s:find_win_by_bufname(s:trans_bufname)

  if l:tw && win_id2win(l:tw) == 0 | let l:tw = 0 | endif
  if l:sw && win_id2win(l:sw) == 0 | let l:sw = 0 | endif

  if l:tw && l:sw
    " Re-apply window-local options/highlights after reopen
    if exists('*deepl#ui#setup_trainer_window')
      silent! call win_execute(l:tw, 'call deepl#ui#setup_trainer_window()')
    endif
    if exists('*deepl#ui#setup_translation_window')
      silent! call win_execute(l:sw, 'call deepl#ui#setup_translation_window()')
    endif

    if exists('*deepl#ui#reflow')
      call deepl#ui#reflow()
    endif
    call win_gotoid(l:cur)
    return
  endif

  " If both windows exist, just reflow and keep focus
  if l:tw && l:sw
    if exists('*deepl#ui#reflow')
      call deepl#ui#reflow()
    endif
    call win_gotoid(l:cur)
    return
  endif

  let g:deepl_ui_right_width     = get(g:, 'deepl_ui_right_width', 59)
  let g:deepl_ui_trainer_height  = get(g:, 'deepl_ui_trainer_height', 12)

  " Create right column
  execute 'vert rightbelow ' . g:deepl_ui_right_width . 'vsplit'
  setlocal winfixwidth

    " Split right column into two horizontal windows
  split

  " Compute dynamic heights for the right column:
  " trainer = ~2/3, translation = ~1/3
  let l:total_h      = winheight(0)
  let l:min_trainer  = get(g:, 'deepl_ui_trainer_min_height', 10)
  let l:min_trans    = get(g:, 'deepl_ui_translation_min_height', 6)

  let l:trainer_h = float2nr(l:total_h * 2.0 / 3.0)

  " Enforce minimum heights
  if l:trainer_h < l:min_trainer
    let l:trainer_h = l:min_trainer
  endif
  if (l:total_h - l:trainer_h) < l:min_trans
    let l:trainer_h = max([l:min_trainer, l:total_h - l:min_trans])
  endif

  " Top: trainer
  wincmd k

  let l:bn = bufnr(s:trainer_bufname)
  if l:bn != -1
    execute 'buffer ' . l:bn
  else
    enew
    execute 'file ' . s:trainer_bufname
  endif

  call s:setup_trainer_window()

  " Bottom: translation
  wincmd j

  let l:bn = bufnr(s:trans_bufname)
  if l:bn != -1
    execute 'buffer ' . l:bn
  else
    enew
    execute 'file ' . s:trans_bufname
  endif

  call s:setup_translation_window()

  " Reflow will apply correct heights and set winfixheight
  if exists('*deepl#ui#reflow')
    call deepl#ui#reflow()
  endif

  call win_gotoid(l:cur)

  " Re-apply window-local translation highlights
  if exists('*deepl#translation_apply_hl')
    call deepl#translation_apply_hl(bufnr('%'))
  endif

endfunction

function! deepl#ui#trainer_winid() abort
  return s:find_win_by_bufname(s:trainer_bufname)
endfunction

function! deepl#ui#translation_winid() abort
  return s:find_win_by_bufname(s:trans_bufname)
endfunction

function! deepl#ui#render_translation(lines) abort
  call deepl#ui#ensure()
  let l:win = deepl#ui#translation_winid()
  if !l:win
    return
  endif

  let l:cur = win_getid()
  call win_gotoid(l:win)

  silent! %delete _
  if type(a:lines) == v:t_list
    call append(0, a:lines)
  else
    call append(0, [string(a:lines)])
  endif
  silent! normal! gg

  call win_gotoid(l:cur)
endfunction

function! deepl#ui#focus_trainer() abort
  call deepl#ui#ensure()
  let l:win = deepl#ui#trainer_winid()
  if l:win
    call win_gotoid(l:win)
  endif
endfunction

function! deepl#ui#focus_translation() abort
  call deepl#ui#ensure()
  let l:win = deepl#ui#translation_winid()
  if l:win
    call win_gotoid(l:win)
  endif
endfunction

"command! DeepLStudyStart call deepl#ui#ensure()
command! DeepLStudyStart  silent! call deepl#ui#ensure() | silent! call deepl#trainer_start()

function! deepl#ui#setup_trainer_window() abort
  call s:setup_trainer_window()
endfunction

function! deepl#ui#setup_translation_window() abort
  call s:setup_translation_window()
endfunction


function! deepl#ui#close() abort
  let l:curid = win_getid()

  let l:tw = deepl#ui#trainer_winid()
  let l:sw = deepl#ui#translation_winid()

  " Close translation window if visible
  if l:sw
    call win_gotoid(l:sw)
    close
  endif

  " Close trainer window if visible
  if l:tw
    call win_gotoid(l:tw)
    close
  endif

  " Restore focus if possible
  if l:curid
    call win_gotoid(l:curid)
  endif
endfunction

command! DeepLStudyClose call deepl#ui#close()

function! deepl#ui#append_translation(lines) abort
  call deepl#ui#ensure()

  if !exists('*deepl#ui#translation_winid')
    return
  endif

  let l:winid = deepl#ui#translation_winid()
  if !l:winid
    return
  endif

  let l:curid = win_getid()
  call win_gotoid(l:winid)

  setlocal modifiable

  " Append with a blank separator if buffer is not empty
  if !(line('$') == 1 && getline(1) ==# '')
    call append(line('$'), '')
  endif

  if type(a:lines) == v:t_list
    call append(line('$'), a:lines)
  else
    call append(line('$'), [string(a:lines)])
  endif

  setlocal nomodifiable

  " Keep view on the latest entry
  normal! G
  let l:header_lnum = search('^#\d\+ ', 'bW')
  if l:header_lnum > 0
    call cursor(l:header_lnum, 1)
    normal! zt
  else
    normal! zb
  endif

  call win_gotoid(l:curid)
endfunction

function! deepl#ui#reflow() abort
  if !exists('*deepl#ui#trainer_winid') || !exists('*deepl#ui#translation_winid')
    return
  endif

  let l:tw = deepl#ui#trainer_winid()
  let l:sw = deepl#ui#translation_winid()
  if !l:tw || !l:sw
    return
  endif

  let l:min_trainer = get(g:, 'deepl_ui_trainer_min_height', 10)
  let l:min_trans   = get(g:, 'deepl_ui_translation_min_height', 6)

  let l:curid = win_getid()

  " Total height of the right column (approx, incl. the horizontal separator)
  let l:total_h = winheight(l:tw) + winheight(l:sw) + 1
  let l:trainer_h = float2nr(l:total_h * 2.0 / 3.0)

  " Enforce minimum heights
  if l:trainer_h < l:min_trainer
    let l:trainer_h = l:min_trainer
  endif
  if (l:total_h - l:trainer_h) < l:min_trans
    let l:trainer_h = max([l:min_trainer, l:total_h - l:min_trans])
  endif

  " Resize trainer window
  call win_gotoid(l:tw)
  setlocal nowinfixheight
  execute 'resize ' . l:trainer_h
  setlocal winfixheight

  " Fix translation window height (remaining space)
  call win_gotoid(l:sw)
  setlocal nowinfixheight
  setlocal winfixheight

  call win_gotoid(l:curid)
endfunction

function! deepl#ui#is_open() abort
  " UI is considered open if any of the tool buffers is visible
  return bufwinnr('__DeepL_Trainer__') != -1 || bufwinnr('__DeepL_Translation__') != -1
endfunction

function! deepl#ui#toggle() abort
  if deepl#ui#is_open()
    silent! call deepl#ui#close()
  else
    silent! call deepl#ui#ensure()
    silent! call deepl#trainer_start()
  endif
endfunction

