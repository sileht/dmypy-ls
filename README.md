# dmypy-ls: super fast mypy language server

It leverages mypy.dmypy_server instead of the slow cli interface

It supports diagnostics only.

## Install

```shell
$ pip install --user dmypy-ls
```

## vim-lspconfig

```lua
lua << EOF
require("lspconfig.configs")["dmypyls"] = {
    default_config = {
        cmd = { 'dmypy-ls' },
        filetypes = { 'python' },
        root_dir = lspconfig.util.root_pattern('pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'Pipfile'),
        single_file_support = true,
    },
}
require("lspconfip").dmypyls.setup({})
EOF
```
