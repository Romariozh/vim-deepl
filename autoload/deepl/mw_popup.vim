"--------------------------------------------------------------"
"  Merriam-Webster Dictionary API popup helpers                " 
"--------------------------------------------------------------"

"---------------------------------------------------------------
" Build popup lines for MW definitions using *raw* JSON stored in the database.
" The output is a list of strings suitable for popup_create()/setline().
function! deepl#mw_popup#build_lines(source, translation, raw_json, width, ...) abort
  " a:1 = src_tag (string, optional)
  " a:2 = ctx_translations (list, optional)
  let l:src_tag = (a:0 >= 1 && type(a:1) == v:t_string) ? a:1 : ''
  let l:alts    = (a:0 >= 2 && type(a:2) == v:t_list) ? a:2 : []

  let l:lines = []

  " 0) Decode raw MW JSON
  let l:entries = s:decode_raw(a:raw_json)
  if type(l:entries) != v:t_list || empty(l:entries)
    let l:hdr  = '[ ' . a:source . ' / ' . a:translation . ' ]'
    let l:pad = float2nr((a:width - strdisplaywidth(l:hdr)) / 2.0)
    if l:pad < 0 | let l:pad = 0 | endif
    return ['', repeat(' ', l:pad) . l:hdr, '', '(no MW data)']
  endif

  " 1) Header line: centered [ word / translation ] with a top padding line
  let l:word = a:source
  " Build translations list: primary + unique alternatives
  let l:trs = []
  if type(a:translation) == v:t_string && !empty(a:translation)
    call add(l:trs, a:translation)
  endif
  for l:t in l:alts
    if type(l:t) == v:t_string && !empty(l:t) && index(l:trs, l:t) < 0
      call add(l:trs, l:t)
    endif
  endfor

  let l:trn = join(l:trs, ', ')
  let l:hdr = '[ ' . l:word . ' / ' . l:trn . ' ]'

  " Add an empty first line (top padding)
  call add(l:lines, '')

  " Center the header within popup width
  let l:pad = float2nr((a:width - strdisplaywidth(l:hdr)) / 2.0)
  if l:pad < 0
    let l:pad = 0
  endif
  call add(l:lines, repeat(' ', l:pad) . l:hdr)

  " Spacer line after header
  call add(l:lines, '')

  " 2) Pick main entry for grammar fields
  let l:main = s:pick_main_entry(l:entries, l:word)
  if type(l:main) != v:t_dict || empty(l:main)
    call add(l:lines, '(no MW entry)')
    return l:lines
  endif

  " Extract headword, pronunciation, stems
  let l:headword = ''
  let l:pron = ''
  let l:hwi = get(l:main, 'hwi', {})
  if type(l:hwi) == v:t_dict
    let l:headword = get(l:hwi, 'hw', '')
    let l:prs = get(l:hwi, 'prs', [])
    if type(l:prs) == v:t_list && !empty(l:prs)
      let l:mw = get(l:prs[0], 'mw', '')
      if type(l:mw) == v:t_string && !empty(l:mw)
        let l:pron = l:mw
      endif
    endif
  endif

  let l:stems = s:extract_stems(l:main)

  " Audio marker: show '-' if no audio ids for this popup
  let l:audio_ids = get(g:, 'deepl_mw_audio_ids', [])
  let l:audio_mark = (type(l:audio_ids) == v:t_list && !empty(l:audio_ids)) ? 'F4' : '-'

  " 3) GRAMMAR line: GRAMMAR: word / headword / [pron] / -
  let l:hw_part = (type(l:headword) == v:t_string && !empty(l:headword)) ? l:headword : '-'
  let l:pr_part = (type(l:pron) == v:t_string && !empty(l:pron)) ? ('[' . l:pron . ']') : '-'
  call add(l:lines, 'GRAMMAR: ' . l:word . ' / ' . l:hw_part . ' / ' . l:pr_part . ' / ' . l:audio_mark)
  call add(l:lines, '')

  " 4) Stems: (indented)
  if type(l:stems) == v:t_list && !empty(l:stems)
    call add(l:lines, '  Stems: ' . join(l:stems, ', '))
    call add(l:lines, '')
  endif
  
  " 4.1) Synonyms: (if present)
  let l:syn_lines = s:extract_synonyms_lines(l:main, a:width)
  if !empty(l:syn_lines)
    call extend(l:lines, l:syn_lines)
    call add(l:lines, '')
  endif

  "4.2) Comparative/Superlative info (from `cxs`) — main entry only.
  let l:cs = s:extract_comp_sup(l:main)
  if !empty(l:cs)
    call extend(l:lines, l:cs)
    call add(l:lines, '')
  endif

  " 5) Definitions grouped by part of speech (fl)
  let l:pos = s:collect_defs_by_fl(l:entries, l:word)
  let l:pos_order = get(l:pos, 'order', [])
  let l:pos_defs  = get(l:pos, 'defs', {})

  for l:fl in l:pos_order
    let l:defs = get(l:pos_defs, l:fl, [])
    if type(l:defs) == v:t_list && !empty(l:defs)
      call add(l:lines, '  ' . s:titlecase_pos(l:fl) . ':')
      for l:d in l:defs
        call add(l:lines, '    - ' . l:d)
      endfor
      call add(l:lines, '')
    endif
  endfor

  " Etymology: Etymology:
  let l:ety = s:extract_etymology(l:main)
  if type(l:ety) == v:t_string && !empty(l:ety)
    call add(l:lines, '  Etymology: ' . l:ety)
    call add(l:lines, '')
  endif
  
  " Trim trailing blank lines
  while len(l:lines) > 0 && l:lines[-1] ==# ''
    call remove(l:lines, -1)
  endwhile

  return l:lines
endfunction
"---------------------------------------------------------------
function! s:titlecase_pos(fl) abort
  " Title-case POS labels like:
  " 'preposition' -> 'Preposition'
  " 'plural noun' -> 'Plural noun' (keep MW style, only capitalize first char)
  if type(a:fl) != v:t_string || empty(a:fl)
    return 'Other'
  endif
  let l:s = a:fl
  return toupper(l:s[0]) . l:s[1:]
endfunction
"---------------------------------------------------------------
" Decode MW raw_json safely.
function! s:decode_raw(raw_json) abort
  " All comments in this file are in English.
  if type(a:raw_json) != v:t_string || empty(a:raw_json)
    return []
  endif
  try
    let l:obj = json_decode(a:raw_json)
  catch
    return []
  endtry
  return type(l:obj) == v:t_list ? l:obj : []
endfunction
"---------------------------------------------------------------
" Normalize a string (lowercase + trim).
function! s:norm(s) abort
  if type(a:s) != v:t_string
    return ''
  endif
  return tolower(trim(a:s))
endfunction
"---------------------------------------------------------------
" Pick a \"main" entry for header/grammar/etymology extraction.
" Preference:
"   1) meta.id matches term OR meta.stems contains term
"   2) first dict entry
"---------------------------------------------------------------
function! s:pick_main_entry(entries, term) abort
  let l:t = s:norm(a:term)

  for l:e in a:entries
    if type(l:e) != v:t_dict
      continue
    endif
    let l:meta = get(l:e, 'meta', {})
    let l:mid  = s:norm(get(l:meta, 'id', ''))
    let l:stems = get(l:meta, 'stems', [])
    if l:mid ==# l:t
      return l:e
    endif
    if type(l:stems) == v:t_list
      for l:s in l:stems
        if s:norm(l:s) ==# l:t
          return l:e
        endif
      endfor
    endif
  endfor

  for l:e in a:entries
    if type(l:e) == v:t_dict
      return l:e
    endif
  endfor

  return {}
endfunction
"---------------------------------------------------------------
" Find an audio id in an entry (hwi.prs[*].sound.audio),
" fallback to uros[*].prs[*].sound.audio.
function! s:find_audio_id(entry) abort
  let l:hwi = get(a:entry, 'hwi', {})
  let l:prs = get(l:hwi, 'prs', [])
  if type(l:prs) == v:t_list
    for l:p in l:prs
      if type(l:p) != v:t_dict
        continue
      endif
      let l:sound = get(l:p, 'sound', {})
      let l:audio = get(l:sound, 'audio', '')
      if type(l:audio) == v:t_string && !empty(l:audio)
        return l:audio
      endif
    endfor
  endif

  let l:uros = get(a:entry, 'uros', [])
  if type(l:uros) == v:t_list
    for l:u in l:uros
      if type(l:u) != v:t_dict
        continue
      endif
      let l:prs2 = get(l:u, 'prs', [])
      if type(l:prs2) == v:t_list
        for l:p2 in l:prs2
          if type(l:p2) != v:t_dict
            continue
          endif
          let l:sound2 = get(l:p2, 'sound', {})
          let l:audio2 = get(l:sound2, 'audio', '')
          if type(l:audio2) == v:t_string && !empty(l:audio2)
            return l:audio2
          endif
        endfor
      endif
    endfor
  endif

  return ''
endfunction
"---------------------------------------------------------------
" Extract stems from meta.stems.
function! s:extract_stems(entry) abort
  let l:meta = get(a:entry, 'meta', {})
  let l:stems = get(l:meta, 'stems', [])
  if type(l:stems) != v:t_list
    return []
  endif
  " Keep it compact.
  return l:stems[:15]
endfunction
"---------------------------------------------------------------
" Cleanup MW markup in text fields (minimal and safe).
function! s:cleanup_text(s) abort
  if type(a:s) != v:t_string
    return ''
  endif

  let l:x = a:s

  " 1) Keep the word inside {sx|word||...} tokens.
  let l:x = substitute(l:x, '{sx|\([^|]*\)|[^}]*}', '\1', 'g')

  " 2) Strip any remaining MW formatting tokens like {bc}, {it}, {ldquo}, {inf}, etc.
  let l:x = substitute(l:x, '{[^}]*}', '', 'g')

  " 3) Normalize whitespace.
  let l:x = substitute(l:x, '\s\+', ' ', 'g')
  return trim(l:x)
endfunction
"---------------------------------------------------------------
function! s:collect_text_tokens(x) abort
  " Recursively collect MW ["text", "..."] items from nested lists.
  let l:out = []

  if type(a:x) == v:t_list
    " Common case: ["text", "..."]
    if len(a:x) >= 2 && type(a:x[0]) == v:t_string && a:x[0] ==# 'text'
      if type(a:x[1]) == v:t_string
        let l:t = s:cleanup_text(a:x[1])
        if !empty(l:t)
          call add(l:out, l:t)
        endif
      endif
      return l:out
    endif

    " Otherwise recurse into children.
    for l:item in a:x
      call extend(l:out, s:collect_text_tokens(l:item))
    endfor
  endif

  return l:out
endfunction
"---------------------------------------------------------------
" Collect definitions grouped by part of speech (entry.fl).
" Uses shortdef for speed and clean output.
function! s:collect_defs_by_fl(entries, term) abort
  " Collect shortdefs grouped by the MW 'fl' (function label / part of speech).
  " Preserve the order of first appearance of each POS.
  let l:pos_defs = {}
  let l:pos_order = []

  for l:e in a:entries
    if type(l:e) != v:t_dict
      continue
    endif

    let l:fl = s:cleanup_text(get(l:e, 'fl', ''))
    if type(l:fl) != v:t_string || empty(l:fl)
      let l:fl = 'other'
    endif

    let l:shortdef = get(l:e, 'shortdef', [])
    if type(l:shortdef) != v:t_list || empty(l:shortdef)
      continue
    endif

    " Register POS in order
    if !has_key(l:pos_defs, l:fl)
      let l:pos_defs[l:fl] = []
      call add(l:pos_order, l:fl)
    endif

    " Append definitions
    for l:d in l:shortdef
      if type(l:d) == v:t_string
        let l:txt = trim(l:d)
        if !empty(l:txt)
          call add(l:pos_defs[l:fl], l:txt)
        endif
      endif
    endfor
  endfor

  " De-duplicate defs per POS, preserve order
  for l:fl in keys(l:pos_defs)
    let l:seen = {}
    let l:out = []
    for l:d in l:pos_defs[l:fl]
      if !has_key(l:seen, l:d)
        let l:seen[l:d] = 1
        call add(l:out, l:d)
      endif
    endfor
    let l:pos_defs[l:fl] = l:out
  endfor

  return {'order': l:pos_order, 'defs': l:pos_defs}
endfunction
"---------------------------------------------------------------
" Extract etymology text from entry.et (best-effort).
function! s:extract_etymology(entry) abort
  let l:et = get(a:entry, 'et', [])
  if type(l:et) != v:t_list || empty(l:et)
    return ''
  endif

  let l:parts = s:collect_text_tokens(l:et)
  let l:out = trim(substitute(join(l:parts, ' '), '\s\+', ' ', 'g'))
  return l:out
endfunction
"---------------------------------------------------------------
" Collect all unique audio ids from MW raw entries 
" (hwi.prs[].sound.audio + uros[].prs[].sound.audio).
function! s:collect_audio_ids(entries) abort
  let l:seen = {}
  let l:out = []

  for l:e in a:entries
    if type(l:e) != v:t_dict
      continue
    endif

    " 1) hwi.prs
    let l:hwi = get(l:e, 'hwi', {})
    let l:prs = get(l:hwi, 'prs', [])
    if type(l:prs) == v:t_list
      for l:p in l:prs
        if type(l:p) != v:t_dict
          continue
        endif
        let l:sound = get(l:p, 'sound', {})
        let l:aid = get(l:sound, 'audio', '')
        if type(l:aid) == v:t_string && !empty(l:aid) && !has_key(l:seen, l:aid)
          let l:seen[l:aid] = 1
          call add(l:out, l:aid)
        endif
      endfor
    endif

    " 2) uros[*].prs
    let l:uros = get(l:e, 'uros', [])
    if type(l:uros) == v:t_list
      for l:u in l:uros
        if type(l:u) != v:t_dict
          continue
        endif
        let l:prs2 = get(l:u, 'prs', [])
        if type(l:prs2) == v:t_list
          for l:p2 in l:prs2
            if type(l:p2) != v:t_dict
              continue
            endif
            let l:sound2 = get(l:p2, 'sound', {})
            let l:aid2 = get(l:sound2, 'audio', '')
            if type(l:aid2) == v:t_string && !empty(l:aid2) && !has_key(l:seen, l:aid2)
              let l:seen[l:aid2] = 1
              call add(l:out, l:aid2)
            endif
          endfor
        endif
      endfor
    endif
  endfor

  return l:out
endfunction
"---------------------------------------------------------------
" Extract MW synonyms paragraphs (entry['syns']).
" Returns list of already-wrapped lines with indentation and '↳' continuations.
function! s:extract_synonyms_lines(entry, width) abort
  let l:syns = get(a:entry, 'syns', [])
  if type(l:syns) != v:t_list || empty(l:syns)
    return []
  endif

  let l:txt_parts = []
  for l:blk in l:syns
    if type(l:blk) != v:t_dict
      continue
    endif
    let l:pt = get(l:blk, 'pt', [])
    if type(l:pt) != v:t_list
      continue
    endif
    for l:item in l:pt
      " Expected: ["text", "..."]
      if type(l:item) == v:t_list && len(l:item) >= 2 && l:item[0] ==# 'text'
        let l:t = s:cleanup_text(l:item[1])
        if !empty(l:t)
          call add(l:txt_parts, l:t)
        endif
      endif
    endfor
  endfor

  if empty(l:txt_parts)
    return []
  endif

  " Join blocks into one paragraph and wrap.
  let l:para = join(l:txt_parts, ' ')
  let l:para = substitute(l:para, '\s\+', ' ', 'g')
  let l:para = trim(l:para)

  " Build first line label + wrap remainder.
  let l:label = '  Synonyms: '
  let l:maxw = max([20, a:width])          " safety
  let l:first_w = l:maxw - strdisplaywidth(l:label)
  if l:first_w < 10
    let l:first_w = 10
  endif

  let l:out = []
  " Use Vim's built-in wrap() if available, else fallback to a dumb split.
  if exists('*strcharpart')
    " wrap() works in Vim 9+, but not always; use split by words
  endif

  " Simple word wrap by words.
  let l:words = split(l:para, '\s\+')
  let l:line = ''
  let l:limit = l:first_w
  for l:w in l:words
    let l:try = empty(l:line) ? l:w : (l:line . ' ' . l:w)
    if strdisplaywidth(l:try) <= l:limit
      let l:line = l:try
    else
      if empty(l:out)
        call add(l:out, l:label . l:line)
      else
        call add(l:out, '↳ ' . l:line)
      endif
      let l:line = l:w
      let l:limit = l:maxw - strdisplaywidth('  ↳ ')
      if l:limit < 10
        let l:limit = 10
      endif
    endif
  endfor

  if !empty(l:line)
    if empty(l:out)
      call add(l:out, l:label . l:line)
    else
      call add(l:out, '↳ ' . l:line)
    endif
  endif

  return l:out
endfunction
"---------------------------------------------------------------
" Extract "comparative of ..." / "superlative of ..." from MW `cxs` (main entry only).
" Returns a list of already-indented lines, e.g.:
"   ["  Comparative of: good", "  Superlative of: ..."]
function! s:extract_comp_sup(entry) abort
  let l:cxs = get(a:entry, 'cxs', [])
  if type(l:cxs) != v:t_list
    return []
  endif

  let l:out = []

  for l:cx in l:cxs
    if type(l:cx) != v:t_dict
      continue
    endif

    let l:cxl = tolower(get(l:cx, 'cxl', ''))
    if l:cxl !=# 'comparative of' && l:cxl !=# 'superlative of'
      continue
    endif

    let l:targets = []
    let l:cxtis = get(l:cx, 'cxtis', [])
    if type(l:cxtis) == v:t_list
      for l:ti in l:cxtis
        if type(l:ti) != v:t_dict
          continue
        endif
        let l:w = s:cleanup_cx_target(get(l:ti, 'cxt', ''))
        if !empty(l:w)
          call add(l:targets, l:w)
        endif
      endfor
    endif

    if empty(l:targets)
      continue
    endif

    let l:label = (l:cxl ==# 'comparative of') ? 'Comparative of' : 'Superlative of'
    call add(l:out, '  ' . l:label . ': ' . join(l:targets, ', '))
  endfor

  return l:out
endfunction

"---------------------------------------------------------------
" Normalize MW cross-reference targets like 'good:1' -> 'good'.
function! s:cleanup_cx_target(s) abort
  let l:x = s:cleanup_text(a:s)
  if empty(l:x)
    return ''
  endif

  " Drop MW homograph suffix: word:1, word:2, ...
  let l:x = substitute(l:x, ':\d\+$', '', '')

  " Drop rare trailing tags like word:noun / word:adj / etc.
  let l:x = substitute(l:x, ':[A-Za-z_]\+\d*$', '', '')

  return l:x
endfunction

"---------------------------------------------------------------
