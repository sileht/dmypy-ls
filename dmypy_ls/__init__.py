#
# Copyright (c) 2021 Mehdi Abaakouk <sileht@sileht.net>
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import argparse
import io
import logging
import os
import re
import time
import traceback
import typing
from contextlib import redirect_stderr, redirect_stdout

from lsprotocol import types
from mypy import dmypy_server
from mypy import main as mypy_main
from mypy.find_sources import SourceFinder
from mypy.fscache import FileSystemCache
from mypy.modulefinder import BuildSource
from mypy.util import hash_digest
from pygls import server

LOG = logging.getLogger(__name__)

class MypyRegexResult(typing.TypedDict):
    file: str
    row: str
    col: str|None
    severity: str
    message: str
    code: str|None


MYPY_OUTPUT_RE = re.compile(
    r"""
        ^
        (?P<file>[^:]+):
        (?P<row>[-+]?\d+):
        (?:(?P<col>[-+]?\d+):)?
        [ ]
        (?P<severity>[^:]+):
        [ ]
        (?P<message>.*?)(?:\ \ \[(?P<code>[^\]]+)\])?
        $
    """,
    re.VERBOSE,
)
MYPY_SEVERITY = {
    "error": types.DiagnosticSeverity.Error,
    "warning": types.DiagnosticSeverity.Warning,
    "note": types.DiagnosticSeverity.Information,
}


class MypyServer(server.LanguageServer):
    def __init__(self) -> None:
        super().__init__("dmypy", "v0.2")
        self._debug = False
        self._use_dmypy = False

        self._flags = [
            "--hide-error-context",
            "--no-color-output",
            "--show-column-numbers",
            "--show-error-codes",
            "--no-error-summary",
            "--no-pretty",
        ]

        self.is_tty = False
        self.terminal_width=80

    def setup(self, debug: bool, use_dmypy: bool = False, virtualenv: str | None = None) -> None:
        self._debug = debug
        self._use_dmypy = use_dmypy

        LOG.info("Initializing mypy options")

        self.fscache = FileSystemCache()

        _, self.options = mypy_main.process_options(
            self._flags, fscache=self.fscache,
            require_targets=False, server_options=self._use_dmypy,
        )

        if virtualenv:
            # NOTE(sileht): This works only if no mypy plugins are used due to:
            # https://github.com/python/mypy/issues/12575
            # The best is to install dmypy-ls in the virtualenv and run it from there
            self.options.python_executable = f"{virtualenv}/bin/python"

        self.finder = SourceFinder(self.fscache, self.options)

        if self._use_dmypy:
            # FIXME(sileht): not ready yet, crash on second check with
            # https://github.com/python/mypy/issues/14645
            self.server = dmypy_server.Server(
                options=self.options,
                status_file="dmypy-ls-not-used-status-file",
            )
            self.options.use_fine_grained_cache = True

            # Use our fscache
            self.server.fscache = self.fscache

            LOG.info("Initializing fined grain cache")
            res = self.server.initialize_fine_grained([], self.is_tty, self.terminal_width)
            self.server.flush_caches()
            self.server.update_stats(res)

    def check(self, source: BuildSource) -> dict[str, typing.Any]:
        self.fscache.flush()
        if source.path and source.text:
            self.fscache.stat(source.path)
            self.fscache.read_cache[source.path] = source.text.encode()
            self.fscache.hash_cache[source.path] = hash_digest(source.text.encode())

        if self._use_dmypy:
            return self.check_with_dmypy(source)

        return self.check_with_mypy(source)

    def check_with_mypy(self, source: BuildSource) -> dict[str, typing.Any]:
        stderr = io.StringIO()
        stdout = io.StringIO()
        with redirect_stderr(stderr):
            with redirect_stdout(stdout):
                t0 = time.time()
                mypy_main.run_build([source], self.options, self.fscache, t0, stdout, stderr)

        return {"out": stdout.getvalue(), "err": stderr.getvalue(), "status": 0}

    def check_with_dmypy(self, source: BuildSource) -> dict[str, typing.Any]:
        if not self.server.following_imports():
            messages = self.server.fine_grained_increment([source])
        else:
            messages = self.server.fine_grained_increment_follow_imports([source])
        res = self.server.increment_output(messages, [source], self.is_tty, self.terminal_width)
        self.server.flush_caches()
        self.server.update_stats(res)
        return res

    def validate(
        self,
        params: (types.DidOpenTextDocumentParams|
            types.DidChangeTextDocumentParams|
            types.DidSaveTextDocumentParams),
    ) -> None:
        started_at = time.monotonic()
        try:
            text_doc = self.workspace.get_document(params.text_document.uri)
            if text_doc.uri.startswith("file://"):
                filepath = os.path.normpath(text_doc.uri[7:])
                name, base_dir = self.finder.crawl_up(filepath)
                source = BuildSource(filepath, name, text_doc.source, base_dir)
            else:
                source = BuildSource(None, None, text_doc.source)
            res = self.check(source)
        except:
            res = {"out": "", "err": traceback.format_exc(), "status": 2}

        elapsed = time.monotonic() - started_at

        LOG.info(f"{source.path} checked in {elapsed}s)")
        self.publish_result_to_diagnostic(text_doc.uri, res, elapsed)

    def publish_result_to_diagnostic(self, uri: str, res: dict[str, typing.Any], elapsed: float) -> None:
        if self._debug or res["err"] or res["status"] != 0:
            LOG.info(f"Ran mypy in {elapsed}s:")
            LOG.info(f"* uri: {uri}")
            LOG.info(f"* result: {res}")

        diagnostics = []
        for line in res["out"].splitlines():
            m = MYPY_OUTPUT_RE.match(line)
            if m is None:
                LOG.info(f"fail to parse mypy result: {line}")
            else:
                data = typing.cast(MypyRegexResult, m.groupdict())
                if not uri.endswith(data["file"]):
                    continue

                code_line = int(data["row"])
                if data["col"] is None:
                    col = 1
                else:
                    col = int(data["col"])
                d = types.Diagnostic(
                    range=types.Range(
                        start=types.Position(line=code_line - 1, character=col - 1),
                        end=types.Position(line=code_line - 1, character=col),
                    ),
                    message=data["message"],
                    code=data["code"],
                    severity=MYPY_SEVERITY[data["severity"]],
                    source="dmypy-ls",
                )
                diagnostics.append(d)

        self.publish_diagnostics(uri, diagnostics)


ls = MypyServer()


@ls.thread()
@ls.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(self: MypyServer, params: types.DidOpenTextDocumentParams) -> None:
    LOG.info(f"DidOpen received {params.text_document.uri}")
    self.validate(params)


@ls.thread()
@ls.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(self: MypyServer, params: types.DidChangeTextDocumentParams) -> None:
    LOG.info(f"Didchange received {params.text_document.uri}")
    self.validate(params)


@ls.thread()
@ls.feature(types.TEXT_DOCUMENT_DID_SAVE)
def did_save(self: MypyServer, params: types.DidSaveTextDocumentParams) -> None:
    LOG.info(f"DidSave received {params.text_document.uri}")
    self.validate(params)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    logging.getLogger("pygls").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description="super fast mypy language server")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--chdir", default="/")
    parser.add_argument("--dmypy-experimental", action="store_true")
    parser.add_argument("--virtualenv")
    args = parser.parse_args()
    LOG.info("chdir into %s", args.chdir)
    os.chdir(args.chdir)
    ls.setup(args.debug, args.dmypy_experimental, args.virtualenv)
    LOG.info("start io loop")
    ls.start_io()
