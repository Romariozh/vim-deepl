function! deepl#hl#apply_trainer() abort
  highlight default DeepLTrainerUnitWord         cterm=bold ctermfg=121 gui=bold
  highlight default DeepLTrainerTranslationWord  cterm=bold ctermfg=221 gui=bold
  highlight default DeepLTrainerModeHard         cterm=bold ctermfg=94  gui=bold
  highlight default DeepLTrainerContextWord      ctermfg=121 gui=NONE cterm=NONE
endfunction

