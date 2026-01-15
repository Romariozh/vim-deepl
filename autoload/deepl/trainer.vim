" autoload/deepl/trainer.vim
scriptencoding utf-8

function! s:center_line(text) abort
  let l:w = winwidth(0)
  let l:t = a:text
  let l:pad = max([0, (l:w - strdisplaywidth(l:t)) / 2])
  return repeat(' ', l:pad) . l:t
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

"--------------------------------------------------------------------------
" --- Trainer mode highlights (bracket tag in header) ---
highlight default DeeplTrainerModeDue  cterm=bold ctermfg=6  gui=bold
highlight default DeeplTrainerModeNew  cterm=bold ctermfg=214 gui=bold
highlight default DeeplTrainerModeHard cterm=bold ctermfg=202 gui=bold
highlight default DeeplTrainerModeDone cterm=bold ctermfg=121 gui=bold

let s:deepl_trainer_mode_ns = 0
let s:deepl_trainer_mode_matchid = {}
"--------------------------------------------------------------------------
function! deepl#trainer#apply_mode_hl(bufnr, lnum, mode_label) abort
  if a:bufnr <= 0 || !bufexists(a:bufnr)
    return
  endif

  " Map label -> highlight group
  let l:grp = ''
  if a:mode_label ==# 'srs_due'
    let l:grp = 'DeeplTrainerModeDue'
  elseif a:mode_label ==# 'srs_new'
    let l:grp = 'DeeplTrainerModeNew'
  elseif a:mode_label ==# 'srs_hard'
    let l:grp = 'DeeplTrainerModeHard'
  elseif a:mode_label ==# 'all done'
    let l:grp = 'DeeplTrainerModeDone'
  else
    " unknown / empty -> clear
    let l:grp = ''
  endif

  " Neovim buffer highlight
  if has('nvim')
    if s:deepl_trainer_mode_ns == 0
      let s:deepl_trainer_mode_ns = nvim_create_namespace('deepl_trainer_mode')
    endif
    call nvim_buf_clear_namespace(a:bufnr, s:deepl_trainer_mode_ns, 0, -1)
    if empty(l:grp) || empty(a:mode_label)
      return
    endif

    let l:line = getbufline(a:bufnr, a:lnum)[0]
    let l:idx = stridx(l:line, a:mode_label)
    if l:idx < 0
      return
    endif
    call nvim_buf_add_highlight(a:bufnr, s:deepl_trainer_mode_ns, l:grp,
          \ a:lnum - 1, l:idx, l:idx + strlen(a:mode_label))
    return
  endif

  " Vim matchaddpos is window-local, so we apply it in the trainer window
  let l:winid = bufwinid(a:bufnr)
  if l:winid == -1
    return
  endif

  " delete old match for that window
  if has_key(s:deepl_trainer_mode_matchid, l:winid)
    silent! call win_execute(l:winid, 'call matchdelete(' . s:deepl_trainer_mode_matchid[l:winid] . ')')
    call remove(s:deepl_trainer_mode_matchid, l:winid)
  endif

  if empty(l:grp) || empty(a:mode_label)
    return
  endif

  let l:line = getbufline(a:bufnr, a:lnum)[0]
  let l:idx = stridx(l:line, a:mode_label)
  if l:idx < 0
    return
  endif

  " add new match in that window
  let l:cmd = 'let w:_deepl_mode_mid = matchaddpos("' . l:grp . '", [['
        \ . a:lnum . ',' . (l:idx + 1) . ',' . strlen(a:mode_label) . ']])'
  call win_execute(l:winid, l:cmd)
  let s:deepl_trainer_mode_matchid[l:winid] = getwinvar(l:winid, '_deepl_mode_mid', -1)
endfunction
"--------------------------------------------------------------------------
function! deepl#trainer#apply_hl(bufnr, word, tr, show) abort
  if a:bufnr <= 0
    return
  endif

  " Minimal highlight groups (colorscheme-safe)
  if !hlexists('DeepLTrainerUnitWord')
    highlight default DeepLTrainerUnitWord cterm=bold ctermfg=121 gui=bold
  endif
  " Same color as DeepLTrainerUnitWord, but NOT bold (for CTX lines)
  if !hlexists('DeepLTrainerCtxWord')
    highlight default DeepLTrainerCtxWord cterm=NONE ctermfg=100 gui=NONE
  endif
  if !hlexists('DeepLTrainerTranslationWord')
    highlight default DeepLTrainerTranslationWord cterm=bold ctermfg=221 gui=bold
  endif
  if !hlexists('DeepLTrainerLabel')
    highlight default DeepLTrainerLabel cterm=bold ctermfg=94 gui=bold
  endif
  if !hlexists('DeepLTrainerGrammarLabel')
    highlight default DeepLTrainerGrammarLabel cterm=bold ctermfg=136 gui=bold
  endif

  if bufnr('%') != a:bufnr
    execute 'silent! buffer ' . a:bufnr
  endif

  " Clear previous window-local matches
  if exists('w:deepl_trainer_match_ids')
    for l:id in w:deepl_trainer_match_ids
      silent! call matchdelete(l:id)
    endfor
  endif
  let w:deepl_trainer_match_ids = []

  " ---- Highlight CARD line: | word / translation |
  " Find first line that looks like card line.
  let l:ln = search('^\s*|\s\+\S', 'nW')
  if l:ln > 0
    let l:line = getline(l:ln)

    " Highlight WORD inside the card line
    if type(a:word) == v:t_string && !empty(a:word)
      let l:p = stridx(l:line, a:word)
      if l:p >= 0
        call add(w:deepl_trainer_match_ids,
              \ matchaddpos('DeepLTrainerUnitWord', [[l:ln, l:p + 1, strlen(a:word)]]))
      endif
    endif

    " Highlight TRANSLATION only when shown
    if a:show && type(a:tr) == v:t_string && !empty(a:tr)
      let l:p2 = stridx(l:line, a:tr)
      if l:p2 >= 0
        call add(w:deepl_trainer_match_ids,
              \ matchaddpos('DeepLTrainerTranslationWord', [[l:ln, l:p2 + 1, strlen(a:tr)]]))
      endif
    endif
  endif

  " ---- Highlight WORD occurrences in CTX1..CTX3 lines using the SAME color as card word
  if type(a:word) == v:t_string && !empty(a:word)
    let l:positions = []

    " case-insensitive whole-word match
    let l:needle = '\c\<'. escape(a:word, '\.^$~[]\') .'\>'

    for l:lnum in range(1, line('$'))
      let l:line = getline(l:lnum)

      " Only CTX lines
      if l:line =~# '^CTX[1-3]:'
        let l:start = 0
        while 1
          let l:m = matchstrpos(l:line, l:needle, l:start)
          if empty(l:m) || l:m[1] < 0
            break
          endif
          call add(l:positions, [l:lnum, l:m[1] + 1, strlen(l:m[0])])
          let l:start = l:m[2]
        endwhile
      endif
    endfor

    if !empty(l:positions)
      " SAME highlight group as the word in the card line
      call add(w:deepl_trainer_match_ids, matchaddpos('DeepLTrainerCtxWord', l:positions))
    endif
  endif

  " ---- Highlight labels: GRAMMAR / CTX1..3
  call add(w:deepl_trainer_match_ids, matchadd('DeepLTrainerGrammarLabel', '^\zsGRAMMAR:\ze', 200))
  call add(w:deepl_trainer_match_ids, matchadd('DeepLTrainerLabel', '^\zsCTX[1-3]:\ze', 200))
endfunction
"--------------------------------------------------------------------------
function! deepl#trainer#next_due_all() abort
  let l:db = expand('~/.local/share/vim-deepl/vocab.db')

  if !filereadable(l:db)
    echohl ErrorMsg | echom 'DLNextDueAll: DB not found: ' . l:db | echohl None
    return
  endif
  if !executable('sqlite3')
    echohl ErrorMsg | echom 'DLNextDueAll: sqlite3 not found in PATH' | echohl None
    return
  endif

  " card_id \t term \t due_ts
  let l:q =
        \ "SELECT c.id, e.term, CAST(c.due_at AS INTEGER) AS due_ts " .
        \ "FROM training_cards c " .
        \ "JOIN entries e ON e.id = c.entry_id " .
        \ "WHERE IFNULL(c.suspended,0)=0 AND c.due_at IS NOT NULL " .
        \ "ORDER BY due_ts ASC LIMIT 1;"

  let l:cmd = 'sqlite3 -separator ' . shellescape("\t") . ' ' . shellescape(l:db) . ' ' . shellescape(l:q)
  let l:out = trim(system(l:cmd))

  if v:shell_error != 0
    echohl ErrorMsg | echom 'DLNextDueAll: sqlite3 error=' . v:shell_error . ' out=' . l:out | echohl None
    return
  endif
  if empty(l:out) || l:out ==# 'NULL'
    echom 'Due: none (no cards with due_at)'
    return
  endif

  let l:parts = split(l:out, "\t")
  if len(l:parts) < 3
    echohl ErrorMsg | echom 'DLNextDueAll: unexpected output: ' . l:out | echohl None
    return
  endif

  let l:card_id = str2nr(l:parts[0])
  let l:term    = l:parts[1]
  let l:ts      = str2nr(l:parts[2])

  " safety: handle milliseconds if ever stored that way
  if l:ts > 20000000000
    let l:ts = float2nr(l:ts / 1000.0)
  endif

  let l:diff = l:ts - localtime()
  if l:diff <= 0
    echom printf('Already due id=%d [%s] since %s',
          \ l:card_id, l:term, strftime('%Y-%m-%d %H:%M', l:ts))
    return
  endif

  let l:d = l:diff / 86400
  let l:h = (l:diff % 86400) / 3600
  let l:m = (l:diff % 3600) / 60

  echom printf('Next due id=%d [%s] in %dd:%02dh:%02dm at %s',
        \ l:card_id, l:term, l:d, l:h, l:m, strftime('%Y-%m-%d %H:%M', l:ts))
endfunction
"--------------------------------------------------------------------------

