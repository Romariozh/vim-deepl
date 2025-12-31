" autoload/deepl/mw_hl.vim
" Highlighting for MW popup. Comments are in English.

function! deepl#mw_hl#ensure_groups() abort
  " Define highlight groups once.
  if exists('g:deepl_mw_hl_defined')
    return
  endif
  let g:deepl_mw_hl_defined = 1

  " Do not use :highlight default here — we want our groups to actually apply.
  if exists('&termguicolors') && &termguicolors
    highlight DeeplMWWord    guifg=#3CB1C3 gui=bold        cterm=bold
    highlight DeeplMWTrn     guifg=#4AB563 gui=bold        cterm=bold
    highlight DeeplMWGrammar guifg=#1BA97F                 cterm=NONE
    highlight DeeplMWStems   guifg=#008080 gui=italic      cterm=italic
    highlight DeeplMWStemsLine gui=italic cterm=italic
    highlight DeeplMWStemsLabel guifg=#13889B ctermfg=30
    highlight DeeplMWPos     guifg=#B4A868                 cterm=NONE
    highlight DeeplMWEty     guifg=#5A8390                 cterm=NONE
    highlight link DeeplMWWrapArrow NonText

  else
    " Fallback for terminals without truecolor: pick reasonable cterm colors.
    " (ctermfg numbers are approximate; tweak if you want)
    highlight DeeplMWWord    ctermfg=114 cterm=bold
    highlight DeeplMWTrn     ctermfg=187 cterm=bold
    highlight DeeplMWGrammar ctermfg=173 cterm=NONE
    highlight DeeplMWStems   ctermfg=30  cterm=italic
    highlight DeeplMWPos     ctermfg=138 cterm=NONE
    highlight DeeplMWEty     ctermfg=241 cterm=NONE
  endif
endfunction


function! deepl#mw_hl#apply(popup_id) abort
  " Apply pattern matches inside the popup window.
  call deepl#mw_hl#ensure_groups()

  " Clear previous matches inside this popup
  call win_execute(a:popup_id, 'silent! call clearmatches()')

  " Header line: [ beside / рядом ]
  " Word part (before '/')
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWWord'', ''\v^\s*\[\s*\zs[^/]+\ze\s*/'')') 
  " Translation part (after '/')
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWTrn'', ''\v^\s*\[\s*[^/]+\s*/\s*\zs[^\]]+\ze\s*\]'')') 

  " GRAMMAR label
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWGrammar'', ''\v^GRAMMAR:\ze'')')

  " Stems: label color only
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWStemsLabel'', ''\v^\s{2}\zsStems:\ze'', 20)')
  " Stems: italic for the whole line
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWStemsLine'', ''\v^\s{2}Stems:.*$'', 10)')

  " Label color only
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWStemsLabel'', ''\v^\s{2}\zs(Synonyms|Stems):\ze'', 20)')
  " Italic for the whole line (first line)
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWStemsLine'', ''\v^\s{2}(Stems|Synonyms):.*$'', 10)')
  " Italic for wrapped continuation lines (↳ ...)
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWStemsLine'', ''\v^\s{2}↳\s.*$'', 10)')

  " Comparative/Superlative: label color & Italic (same as Stems label style)
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWStemsLabel'', ''\v^\s{2}\zs(Comparative of|Superlative of):\ze'', 20)')
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWStemsLine'', ''\v^\s{2}(Comparative of|Superlative of):.*$'', 10)')


  " POS headings: two-space indent + text + ":" at end, notice: exclude Stems/Etymology
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWPos'', ''\v^\s{2,}.*:\s*$'', 20)')
  " Etymology line (if present)
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWEty'', ''\v^\s{2}Etymology:.*$'')')
  
  " Continuation lines produced by manual wrapping: "↳ ..."
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWStemsLine'', ''\v^\s*↳\s.*$'', 10)')
  " Make the arrow look like Vim showbreak (grey)
  call win_execute(a:popup_id, 'silent! call matchadd(''DeeplMWWrapArrow'', ''\v^\s*\zs↳\ze'', 30)')


endfunction

