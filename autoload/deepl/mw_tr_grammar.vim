" autoload/deepl/mw_tr_grammar.vim
" Trainer-only MW grammar extractor (do NOT affect popup).

if exists('g:loaded_deepl_mw_tr_grammar')
  finish
endif
let g:loaded_deepl_mw_tr_grammar = 1

" Returns dict:
"  {
"    'headword': 'trav*el',
"    'pron': 'ˈtrav-əl',
"    'audio_mark': 'F4' or '-'
"  }
"
" Side-effects (needed for F4 audio handler):
"   - sets g:deepl_mw_audio_ids (list)
"   - sets g:deepl_mw_audio_idx (number)
function! deepl#mw_tr_grammar#extract(word, mw_defs) abort
  let l:out = {'headword': '', 'pron': '', 'audio_mark': '-'}

  " Defensive defaults for F4 handler
  let g:deepl_mw_audio_ids = []
  let g:deepl_mw_audio_idx = 0

  if type(a:mw_defs) != v:t_dict || empty(a:mw_defs)
    return l:out
  endif

  " 1) Audio ids come from backend (table mw_definitions.audio_ids)
  let l:audio_ids = get(a:mw_defs, 'audio_ids', [])
  if type(l:audio_ids) == v:t_list
    let g:deepl_mw_audio_ids = l:audio_ids
  else
    let g:deepl_mw_audio_ids = []
  endif
  let g:deepl_mw_audio_idx = 0

  let l:out.audio_mark = (len(g:deepl_mw_audio_ids) > 0) ? 'voice' : '-'

  " 2) Parse raw_json to extract headword/pron
  let l:raw_json = get(a:mw_defs, 'raw_json', '')
  if type(l:raw_json) != v:t_string || empty(l:raw_json)
    return l:out
  endif

  if !exists('*json_decode')
    return l:out
  endif

  let l:entries = []
  try
    let l:entries = json_decode(l:raw_json)
  catch
    let l:entries = []
  endtry

  if type(l:entries) != v:t_list || empty(l:entries)
    return l:out
  endif

  " Trainer-simple main entry: first dict element
  let l:main = l:entries[0]
  if type(l:main) != v:t_dict || empty(l:main)
    return l:out
  endif

  " headword / pron (same logic as popup)
  let l:hwi = get(l:main, 'hwi', {})
  if type(l:hwi) == v:t_dict
    let l:hw = get(l:hwi, 'hw', '')
    if type(l:hw) == v:t_string
      let l:out.headword = l:hw
    endif

    let l:prs = get(l:hwi, 'prs', [])
    if type(l:prs) == v:t_list && !empty(l:prs)
      let l:mw = get(l:prs[0], 'mw', '')
      if type(l:mw) == v:t_string && !empty(l:mw)
        let l:out.pron = l:mw
      endif
    endif
  endif

  return l:out
endfunction

