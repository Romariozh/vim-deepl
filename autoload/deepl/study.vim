function! deepl#study#start() abort
  " Open UI layout
  silent! call deepl#ui#ensure()

  " Focus trainer window (top-right) if possible
  if exists('*deepl#ui#trainer_winid')
    let l:tw = deepl#ui#trainer_winid()
    if l:tw
      call win_gotoid(l:tw)
    endif
  endif

  " Start trainer in the current window (should be trainer window)
  silent! execute 'DeepLTrainerStart'
endfunction

