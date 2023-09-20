dmypy-ls: mypy language server
==============================

It is a PoC language server using dmypy internal API, not intended to be daily used.

It supports diagnostics only.

The first run is very slow as dmypy has to build a cache, and dmypy rebuilds the cache for no obvious reason
when multiple files are opened.

These days `mypy` cli is still faster because of the efficient disk cache it has now.

Install
-------

.. code:: shell

   $ pip install --user dmypy-ls

vim-lspconfig
-------------

.. code:: lua

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
