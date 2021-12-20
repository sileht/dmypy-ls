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

import os
import argparse
from contextlib import redirect_stderr
from contextlib import redirect_stdout
import io
import re
import tempfile
import time
import typing

import mypy
from mypy import dmypy_server
from pygls import server
from pygls.lsp import methods
from pygls.lsp import types


MYPY_OUTPUT_RE = re.compile(
    r"[^:]+:(?P<row>[-+]?\d+):(?P<col>[-+]?\d+): (?P<severity>[^:]+): (?P<message>.*)  \[(?P<code>[^\]]+)\]"
)
MYPY_SEVERITY = {
    "error": types.DiagnosticSeverity.Error,
    "warning": types.DiagnosticSeverity.Warning,
    "note": types.DiagnosticSeverity.Information,
}


class MypyServer(server.LanguageServer):
    def __init__(self) -> None:
        super().__init__()

        self._status_file: typing.Optional[str] = tempfile.NamedTemporaryFile(
            prefix="dmypy-ls-status-"
        ).name
        self._flags = [
            "--hide-error-codes",
            "--hide-error-context",
            "--no-color-output",
            "--show-column-numbers",
            "--show-error-codes",
            "--no-error-summary",
            "--no-pretty",
        ]
        _, options = mypy.main.process_options(
            ["-i"] + self._flags, require_targets=False, server_options=True
        )
        self._mypy = dmypy_server.Server(options=options, status_file=self._status_file)

    def __del__(self) -> None:
        self._status_file = None

    async def validate(
        self,
        params: typing.Union[
            types.DidOpenTextDocumentParams,
            types.DidChangeTextDocumentParams,
            types.DidSaveTextDocumentParams,
        ],
    ) -> None:
        text_doc = self.workspace.get_document(params.text_document.uri)
        with tempfile.NamedTemporaryFile(prefix="dmypy-ls-source-") as f:
            args = []
            args.extend(self._flags)
            if text_doc.uri.startswith("file://"):
                filepath = text_doc.uri[7:]
                args.append(filepath)
            else:
                f.write(text_doc.source.encode())
                f.flush()
                args.append(f.name)

            started_at = time.monotonic()
            stderr = io.StringIO()
            stdout = io.StringIO()
            with redirect_stderr(stderr):
                with redirect_stdout(stdout):
                    sources, options = mypy.main.process_options(
                        ["-i"] + list(args),
                        require_targets=True,
                        server_options=True,
                        fscache=self._mypy.fscache,
                        program="pmypy-ls",
                        header=argparse.SUPPRESS,
                    )

                    # Set content of the file instead reading it from the disk
                    # unlike shadow-file this works with the fscache correctly
                    if text_doc.uri.startswith("file://"):
                        if hasattr(self._mypy, "fswatcher"):
                            self._mypy.fswatcher.update_changed(remove=[filepath], update=[])
                        filepath = text_doc.uri[7:]
                        for source in sources:
                            if source.path == filepath:
                                source.text = text_doc.source

                    try:
                        resp = self._mypy.check(sources, is_tty=False, terminal_width=80)
                    except BaseException:
                        resp = {"out": "", "err": ""}

            elapsed = time.monotonic() - started_at
            self.show_message(f"ran mypy in {elapsed}s, stdout: {stdout.getvalue()}, stderr: {stderr.getvalue()}, out={resp['out']}")

            lines = [
                line.strip()
                for line in resp["err"].split("\n") + resp["out"].split("\n")
                if line.strip()
            ]
            diagnostics = []
            for line in lines:
                m = MYPY_OUTPUT_RE.match(line)
                if m is None:
                    self.show_message(f"fail to parse mypy result: {line}")
                    self.show_message_log(f"fail to parse mypy result: {line}")
                else:
                    data = m.groupdict()
                    line = int(data["row"])
                    col = int(data["col"])
                    d = types.Diagnostic(
                        range=types.Range(
                            start=types.Position(line=line - 1, character=col - 1),
                            end=types.Position(line=line - 1, character=col),
                        ),
                        message=data["message"],
                        code=data["code"],
                        severity=MYPY_SEVERITY[data["severity"]],
                        source="dmypy-ls",
                    )
                    diagnostics.append(d)

            self.publish_diagnostics(text_doc.uri, diagnostics)


ls = MypyServer()


@ls.feature(methods.TEXT_DOCUMENT_DID_OPEN)
async def did_open(self: MypyServer, params: types.DidOpenTextDocumentParams) -> None:
    await self.validate(params)

# TODO(sileht): mypy FineGrainedBuildManager need first to be fixed to use
# BuildSource with text passed to dmypy_server.Server instead of rereading the
# file from disk
#@ls.feature(methods.TEXT_DOCUMENT_DID_CHANGE)
#async def did_change(
#    self: MypyServer, params: types.DidChangeTextDocumentParams
#) -> None:
#    await self.validate(params)

@ls.feature(methods.TEXT_DOCUMENT_DID_SAVE)
async def did_save(
    self: MypyServer, params: types.DidSaveTextDocumentParams
) -> None:
    await self.validate(params)

def main() -> None:
    os.chdir("/")
    ls.start_io()  # type: ignore[no-untyped-call]
