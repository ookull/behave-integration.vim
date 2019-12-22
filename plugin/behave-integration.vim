if !has("python3")
  echo "vim has to be compiled with +python3 to run this"
  finish
endif

if exists('g:behave_integration_plugin_loaded')
  finish
endif

let s:plugin_root_dir = fnamemodify(resolve(expand('<sfile>:p')), ':h')

python3 << EOF
import sys
from os.path import normpath, join
import vim
plugin_root_dir = vim.eval('s:plugin_root_dir')
python_root_dir = normpath(join(plugin_root_dir, '..', 'python'))
sys.path.insert(0, python_root_dir)
import plugin
EOF

let g:behave_integration_plugin_loaded = 1

function! BehaveJump()
  python3 plugin.behave_jump()
endfunction

command! -nargs=0 BehaveJump call BehaveJump()

function! BehaveErrors()
  python3 plugin.behave_errors()
endfunction

command! -nargs=0 BehaveErrors call BehaveErrors()
