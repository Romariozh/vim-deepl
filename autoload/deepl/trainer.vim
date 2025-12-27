
" autoload/deepl/trainer.vim
scriptencoding utf-8

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
" Green background for 'all done'
highlight! DeeplTrainerAllDone guifg=#83C092 guibg=#384B55 ctermfg=0 ctermbg=0

let s:deepl_trainer_mode_ns = 0
let s:deepl_trainer_mode_matchid = {}
"--------------------------------------------------------------------------
function! deepl#trainer#apply_mode_hl(bufnr, lnum, text) abort
  if a:bufnr <= 0 || !bufexists(a:bufnr)
    return
  endif

  " Neovim buffer highlight
  if has('nvim')
    if s:deepl_trainer_mode_ns == 0
      let s:deepl_trainer_mode_ns = nvim_create_namespace('deepl_trainer_mode')
    endif
    call nvim_buf_clear_namespace(a:bufnr, s:deepl_trainer_mode_ns, 0, -1)
    if empty(a:text)
      return
    endif

    let l:line = getbufline(a:bufnr, a:lnum)[0]
    let l:idx = stridx(l:line, a:text)
    if l:idx < 0
      return
    endif
    call nvim_buf_add_highlight(a:bufnr, s:deepl_trainer_mode_ns, 'DeeplTrainerAllDone',
          \ a:lnum - 1, l:idx, l:idx + strlen(a:text))
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

  if empty(a:text)
    return
  endif

  let l:line = getbufline(a:bufnr, a:lnum)[0]
  let l:idx = stridx(l:line, a:text)
  if l:idx < 0
    return
  endif

  " add new match in that window (lnum is 1-based, col is 1-based)
  let l:cmd = 'let w:_deepl_mode_mid = matchaddpos("DeeplTrainerAllDone", [['
        \ . a:lnum . ',' . (l:idx + 1) . ',' . strlen(a:text) . ']])'
  call win_execute(l:winid, l:cmd)
  let s:deepl_trainer_mode_matchid[l:winid] = getwinvar(l:winid, '_deepl_mode_mid', -1)
endfunction

"--------------------------------------------------------------------------
function! deepl#trainer#apply_hl(bufnr, word, tr, show) abort
  " Must run in trainer window context (call via win_execute()).
  if a:bufnr <= 0
    return
  endif

  " Ensure highlight groups exist (colorscheme may reset them)
  if !hlexists('DeepLTrainerUnitWord')
    highlight default DeepLTrainerUnitWord cterm=bold ctermfg=121 gui=bold
  endif
  if !hlexists('DeepLTrainerTranslationWord')
    highlight default DeepLTrainerTranslationWord cterm=bold ctermfg=221 gui=bold
  endif
  if !hlexists('DeepLTrainerModeHard')
    highlight default DeepLTrainerModeHard cterm=bold ctermfg=94 gui=bold
  endif
  if !hlexists('DeepLTrainerContextWord')
    highlight default DeepLTrainerContextWord ctermfg=121 gui=NONE cterm=NONE
  endif

  " Labels UNIT/TRN/CTX
  if !hlexists('DeepLTrainerLabel')
    highlight default DeepLTrainerLabel cterm=bold ctermfg=208 gui=bold
  endif

  " --- Grammar highlight groups ---
  if !hlexists('DeepLTrainerGrammarTitle')
    highlight default DeepLTrainerGrammarTitle cterm=bold ctermfg=208 gui=bold
  endif
  if !hlexists('DeepLTrainerGrammarKey')
    highlight default DeepLTrainerGrammarKey cterm=bold ctermfg=215 gui=bold
  endif
  if !hlexists('DeepLTrainerGrammarPos')
    highlight default DeepLTrainerGrammarPos cterm=bold ctermfg=121 gui=bold
  endif
  if !hlexists('DeepLTrainerGrammarBullet')
    highlight default DeepLTrainerGrammarBullet cterm=bold ctermfg=221 gui=bold
  endif
  if !hlexists('DeepLTrainerGrammarMore')
    highlight default DeepLTrainerGrammarMore cterm=bold ctermfg=244 gui=bold
  endif
  if !hlexists('DeepLTrainerGrammarEtymology')
    highlight default DeepLTrainerGrammarEtymology cterm=italic ctermfg=180 gui=italic
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

  " Highlight UNIT value
  let l:ln = search('^UNIT:\s*', 'nW')
  if l:ln > 0 && !empty(a:word)
    let l:line = getline(l:ln)
    let l:col0 = matchend(l:line, '^UNIT:\s*') + 1
    if l:col0 > 0
      call add(w:deepl_trainer_match_ids,
            \ matchaddpos('DeepLTrainerUnitWord', [[l:ln, l:col0, strlen(a:word)]]))
    endif
  endif

  " Highlight TRN value (only when shown)
  if a:show
    let l:ln = search('TRN:\s*', 'nW')
    if l:ln > 0 && !empty(a:tr)
      let l:line = getline(l:ln)
      let l:col0 = matchend(l:line, 'TRN:\s*') + 1
      if l:col0 > 0
        call add(w:deepl_trainer_match_ids,
              \ matchaddpos('DeepLTrainerTranslationWord', [[l:ln, l:col0, strlen(a:tr)]]))
      endif
    endif
  endif

  " Highlight word occurrences in CTX lines (word only)
  if !empty(a:word)
    let l:positions = []
    let l:needle = '\<' . escape(a:word, '\.^$~[]') . '\>'

    " Find CTX line, then also process continuation lines (6 leading spaces)
    let l:ln = search('^CTX:\s*', 'nW')
    while l:ln > 0
      let l:line = getline(l:ln)

      if l:line =~# '^CTX:\s*' || l:line =~# '^\s\{6}'
        let l:start = 0
        while 1
          let l:m = matchstrpos(l:line, l:needle, l:start)
          if empty(l:m) || l:m[1] < 0
            break
          endif
          call add(l:positions, [l:ln, l:m[1] + 1, strlen(l:m[0])])
          let l:start = l:m[2]
        endwhile
        let l:ln += 1
        continue
      endif

      break
    endwhile

    if !empty(l:positions)
      call add(w:deepl_trainer_match_ids, matchaddpos('DeepLTrainerContextWord', l:positions))
    endif
  endif
  
  " Highlight labels UNIT/TRN/CTX
  call add(w:deepl_trainer_match_ids, matchadd('DeepLTrainerLabel', '^\zsUNIT:\ze', 200))
  call add(w:deepl_trainer_match_ids, matchadd('DeepLTrainerLabel', '^\zsCTX:\ze', 200))
  " TRN может быть в середине строки
  call add(w:deepl_trainer_match_ids, matchadd('DeepLTrainerLabel', '\<TRN:\ze', 200))

  " --- GRAMMAR highlights ---

  " Title line
  call add(w:deepl_trainer_match_ids,
        \ matchadd('DeepLTrainerGrammarTitle', '^GRAMMAR:\s*$', 120))

  " Keys (match anywhere in line, not only at start)
  call add(w:deepl_trainer_match_ids, matchadd('DeepLTrainerGrammarKey', '\<Word\ze:\s', 110))
  call add(w:deepl_trainer_match_ids, matchadd('DeepLTrainerGrammarKey', '\<Stems\ze:\s', 110))
  call add(w:deepl_trainer_match_ids, matchadd('DeepLTrainerGrammarKey', '\<Basic definitions\ze:\s*', 110))
  call add(w:deepl_trainer_match_ids, matchadd('DeepLTrainerGrammarKey', '\<Etymology\ze:\s*', 110))

" POS-like headers: any "  Something:" line (keys are highlighted with higher priority)
  call add(w:deepl_trainer_match_ids,
        \ matchadd('DeepLTrainerGrammarPos',
        \ '^\s\{2}\zs.\{-}\ze:\s*$', 90)) 
  " POS value on same line: "... Part of Speech: Noun"
  " call add(w:deepl_trainer_match_ids,
        \ matchadd('DeepLTrainerGrammarPos', 'Part of Speech:\s*\zs\w\+', 80))

  " Bullets "- ..."
  call add(w:deepl_trainer_match_ids,
        \ matchadd('DeepLTrainerGrammarBullet', '^\s\{4}\zs-\ze\s', 95)) 

  " “… (+N more)”
  call add(w:deepl_trainer_match_ids,
        \ matchadd('DeepLTrainerGrammarMore', '^\s\{4}…\s*(+\d\+\s\+more)', 96)) 

  " --- Etymology block highlight (from '  Etymology:' until blank line) ---
  let l:ety_ln = search('^\s\{2}Etymology:\s', 'nW')
  if l:ety_ln > 0
    let l:ln = l:ety_ln
    while l:ln > 0
      let l:line = getline(l:ln)
      if empty(l:line)
        break
      endif

      " stop if we left grammar block (Mastery/Keys/Separator etc)
      if l:line =~# '^\%(Mastery:\|Keys:\|-\{5,}\)'
        break
      endif

      " highlight full line
      call add(w:deepl_trainer_match_ids,
            \ matchaddpos('DeepLTrainerGrammarEtymology', [[l:ln, 1, strlen(l:line)]], 85))

      let l:ln += 1
    endwhile
  endif

  " Highlight Hard:1 in header if present
  call add(w:deepl_trainer_match_ids,
        \ matchadd('DeepLTrainerModeHard', 'Hard:\s*1'))

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

