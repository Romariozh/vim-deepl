vim9script

if !exists('g:vimdeepl_curl')
  g:vimdeepl_curl = executable('/usr/bin/curl') ? '/usr/bin/curl' : 'curl'
endif

if !exists('g:vimdeepl_bookmarks_debug')
  g:vimdeepl_bookmarks_debug = 0
endif

# FastAPI base URL
if !exists('g:vimdeepl_api_base')
  g:vimdeepl_api_base = 'http://127.0.0.1:8787'
endif

# Enable/disable bookmarks feature
if !exists('g:vimdeepl_bookmarks_enabled')
  g:vimdeepl_bookmarks_enabled = 1
endif

def EnsureHighlights()
  # One unified style: background only
  silent! execute 'highlight VimDeeplBookMark ctermbg=23 guibg=#1f4a4e'
enddef

def EnsurePropTypes()
  EnsureHighlights()

  if empty(prop_type_get('vimdeepl_bm'))
    prop_type_add('vimdeepl_bm', {'highlight': 'VimDeeplBookMark'})
  endif
enddef

def PropType(kind: string): string
  return 'vimdeepl_bm'
enddef

def CanonPath(): string
  return resolve(expand('%:p'))
enddef

def WordSpanAtCursor(): dict<any>
  var l = getline('.')
  var bcol = col('.') - 1        # 0-based byte column
  if bcol < 0 || bcol >= strlen(l)
    return {}
  endif

  # If cursor not on a keyword char, do nothing
  if match(strpart(l, bcol, 1), '\k') < 0
    return {}
  endif

  var start = bcol
  while start > 0 && match(strpart(l, start - 1, 1), '\k') >= 0
    start -= 1
  endwhile

  var endc = bcol
  while endc < strlen(l) && match(strpart(l, endc, 1), '\k') >= 0
    endc += 1
  endwhile

  var term = strpart(l, start, endc - start)
  return { col: start + 1, length: endc - start, term: term }
enddef


def CurlJson(method: string, url: string, body: string): string
  var cmd = g:vimdeepl_curl .. ' -sS'
  cmd ..= ' -X ' .. shellescape(method)
  cmd ..= ' -H ' .. shellescape('Content-Type: application/json')
  cmd ..= ' ' .. shellescape(url)

  if body !=# ''
    cmd ..= ' --data ' .. shellescape(body)
  endif

  if g:vimdeepl_bookmarks_debug
    echom '[vim-deepl bookmarks] ' .. cmd
  endif

  var out = system(cmd)

  if g:vimdeepl_bookmarks_debug
    echom '[vim-deepl bookmarks] out=' .. out
  endif

  return out
enddef

def CurlGet(url: string): string
  var cmd = g:vimdeepl_curl .. ' -sS ' .. shellescape(url)

  if g:vimdeepl_bookmarks_debug
    echom '[vim-deepl bookmarks] ' .. cmd
  endif

  var out = system(cmd)

  if g:vimdeepl_bookmarks_debug
    echom '[vim-deepl bookmarks] out=' .. out
  endif

  return out
enddef

export def MarkAtCursor(kind: string)
  if !g:vimdeepl_bookmarks_enabled
    return
  endif

  EnsurePropTypes()

  if &buftype !=# '' || expand('%:p') ==# ''
    return
  endif

  var span = WordSpanAtCursor()
  if empty(span)
    return
  endif

  var lnum = line('.')
  var coln = span.col
  var length = span.length
  var term = span.term

  if term ==# ''
    return
  endif

  var payload = json_encode({
    path: CanonPath(),
    lnum: lnum,
    col: coln,
    length: length,
    term: term,
    kind: kind,
  })

  var url = g:vimdeepl_api_base .. '/bookmarks/mark'
  var out = CurlJson('POST', url, payload)

  var obj: any
  try
    obj = json_decode(out)
  catch
    return
  endtry

  if type(obj) != v:t_dict || !has_key(obj, 'id')
    return
  endif
  
  # Remove only an existing property exactly at the same span (same line/col/len)
  for p in prop_list(lnum, {'type': PropType(kind)})
    if get(p, 'col', -1) == coln && get(p, 'length', -1) == length
      # prop_remove() range is line-based; delete by id on this line only
      prop_remove({'type': PropType(kind), 'id': p['id']}, lnum, lnum)
    endif
  endfor

  # Add highlight (no explicit id)
  prop_add(lnum, coln, {type: PropType(kind), length: length})

enddef

export def ApplyForBuffer()
  if !g:vimdeepl_bookmarks_enabled
    return
  endif

  EnsurePropTypes()

  if &buftype !=# '' || expand('%:p') ==# ''
    return
  endif

  # Clear existing highlights in this buffer
  prop_remove({type: 'vimdeepl_bm', all: v:true})

  var path = CanonPath()
  var url = g:vimdeepl_api_base .. '/bookmarks/list?path=' .. UrlEncode(path)
  var out = CurlGet(url)

  var obj: any
  try
    obj = json_decode(out)
  catch
    return
  endtry

  if type(obj) != v:t_dict || !has_key(obj, 'marks')
    return
  endif

  for m in obj['marks']
    var lnum = m['lnum']
    var coln = m['col']
    var length = m['length']
    var term = m['term']
    var kind = m['kind']
    var id = m['id']

    if lnum < 1 || lnum > line('$')
      continue
    endif

    var lineText = getline(lnum)
    if coln < 1 || (coln - 1 + length) > strlen(lineText)
      continue
    endif

    # Strict check (you said the book text is immutable)
    if strpart(lineText, coln - 1, length) !=# term
      continue
    endif

    prop_add(lnum, coln, {type: PropType(kind), length: length})
  endfor
enddef

def UrlEncode(s: string): string
  # RFC 3986-ish percent-encoding for query parameter values.
  # Keep unreserved: ALPHA / DIGIT / "-" / "." / "_" / "~"
  var out = ''
  for ch in split(s, '\zs')
    var c = char2nr(ch)
    if (c >= char2nr('a') && c <= char2nr('z'))
      || (c >= char2nr('A') && c <= char2nr('Z'))
      || (c >= char2nr('0') && c <= char2nr('9'))
      || ch ==# '-' || ch ==# '.' || ch ==# '_' || ch ==# '~'
      out ..= ch
    else
      out ..= printf('%%%02X', c)
    endif
  endfor
  return out
enddef

# Wrappers to integrate with existing mappings without touching your functions.
export def TranslateWordAndMark()
  deepl#translate_word()
  MarkAtCursor('f2')
enddef

export def ShowDefsAndMark()
  var before = getpos('.')     # position where user requested defs

  deepl#show_defs()

  var after = getpos('.')      # show_defs() may move cursor (e.g. to column 1)

  # Mark the originally requested word, not the post-show_defs position
  call setpos('.', before)
  MarkAtCursor('mw')

  # Restore final cursor position (keep existing behavior)
  call setpos('.', after)
enddef

