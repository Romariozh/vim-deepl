let s:mw_audio_job = -1

function! deepl#mw_audio#play_cached(path) abort
  if type(a:path) != v:t_string || empty(a:path)
    echo "MW audio: empty path"
    return
  endif

  " Hard defaults: no .vimrc required.
  let l:sock   = '/tmp/pulse-native'
  let l:player = 'mplayer'
  let l:ao     = 'pulse'
  let l:reps   = 2
  let l:delay  = 1

  if getftype(l:sock) !=# 'socket'
    echo "MW audio: pulse socket not found: " . l:sock
    return
  endif

  let l:path = shellescape(a:path)
  let l:env  = 'PULSE_SERVER=unix:' . shellescape(l:sock)

  let l:cmd = l:env . ' ' . l:player . ' -really-quiet -ao ' . l:ao . ' ' . l:path
  let l:script = l:cmd . '; sleep ' . l:delay . '; ' . l:cmd

  " Run in background (no blocking).
  if exists('*job_start')
    " Stop previous playback job (best-effort).
    if type(s:mw_audio_job) == v:t_number && s:mw_audio_job > 0
      try
        call job_stop(s:mw_audio_job, 'term')
      catch
      endtry
    endif
    let s:mw_audio_job = job_start(['sh', '-lc', l:script], {'out_io': 'null', 'err_io': 'null'})
  else
    call system(l:script . ' >/dev/null 2>&1 &')
  endif
endfunction

" Request MW audio via backend, then play cached file through Pulse tunnel.
"
" Arguments:
" - http_post_json_func: Funcref to a function(url, payload) -> dict
" - api_base: g:deepl_api_base
" - audio_ids: g:deepl_mw_audio_ids
" - audio_idx: g:deepl_mw_audio_idx
"
" Returns: new audio_idx (cycled if multiple ids exist)
function! deepl#mw_audio#handle_f4(http_post_json_func, api_base, audio_ids, audio_idx) abort
  if empty(a:api_base)
    echo "MW audio: g:deepl_api_base is not set"
    return a:audio_idx
  endif

  if type(a:audio_ids) != v:t_list || empty(a:audio_ids)
    echo "No MW audio for this entry"
    return a:audio_idx
  endif

  " Normalize index.
  let l:idx = a:audio_idx
  if l:idx < 0 || l:idx >= len(a:audio_ids)
    let l:idx = 0
  endif

  let l:audio_id = a:audio_ids[l:idx]
  let l:url = a:api_base . '/mw/audio/play'
  let l:payload = {'audio_id': l:audio_id, 'play_server': v:false}

  " Debounce to avoid double-triggering (popup filter/mapping quirks).
  if !exists('g:deepl_mw_audio_last_t')
    let g:deepl_mw_audio_last_t = reltime()
  else
    if reltimefloat(reltime(g:deepl_mw_audio_last_t)) < 0.25
      return (l:idx + 1) % len(a:audio_ids)
    endif
    let g:deepl_mw_audio_last_t = reltime()
  endif
  " Ask backend to ensure audio is cached (and try to play server-side if it can).
  try
    let l:r = call(a:http_post_json_func, [l:url, l:payload])
  catch
    echo "MW audio request failed"
    return l:idx
  endtry

  if type(l:r) != v:t_dict
    echo "MW audio: bad response"
    return l:idx
  endif

  let l:path = get(l:r, 'cached_path', '')
  if type(l:path) != v:t_string || empty(l:path)
    echo "MW audio: missing cached_path"
    return l:idx
  endif

  echo 'MW audio: ' . l:audio_id
  " Play locally in this SSH session via Pulse forwarded socket.
  call deepl#mw_audio#play_cached(l:path)

  " Cycle to next audio id on each press.
  return (l:idx + 1) % len(a:audio_ids)
endfunction

