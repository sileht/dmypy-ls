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

import tempfile
import typing
from unittest import mock

from pygls import workspace
from pygls.lsp import types
import pytest

import dmypy_ls


@pytest.fixture
def fake_document() -> workspace.Document:
    with tempfile.NamedTemporaryFile(prefix="dmypy-ls-tests-") as f:
        # ensure the real file is not read
        f.write(b"import os")
        f.flush()

        fake_document_uri = f"file://{f.name}"
        fake_document_content = """
def foo(bar: str) -> int:
    return "not a int"

foo(5)
        """
        yield workspace.Document(fake_document_uri, fake_document_content)  # type: ignore[no-untyped-call]


class ServerFixture(typing.NamedTuple):
    server: dmypy_ls.MypyServer
    fake_publish_diagnostics: mock.Mock


@pytest.fixture
def server(fake_document: workspace.Document) -> ServerFixture:
    fake_publish_diagnostics = mock.Mock()
    s = dmypy_ls.MypyServer()
    s.lsp.workspace = workspace.Workspace("", None)  # type: ignore[no-untyped-call,assignment]
    s.lsp.workspace.get_document = mock.Mock(return_value=fake_document)  # type: ignore[assignment]
    s.lsp.transport = mock.Mock()
    s.publish_diagnostics = fake_publish_diagnostics  # type: ignore[assignment]
    return ServerFixture(s, fake_publish_diagnostics)


def _assert_diags(diags: typing.List[types.Diagnostic]) -> None:
    assert len(diags) == 2
    assert (
        diags[0].message == 'Incompatible return value type (got "str", expected "int")'
    )
    assert diags[0].code == "return-value"
    assert diags[0].severity == types.DiagnosticSeverity.Error
    assert (
        diags[1].message
        == 'Argument 1 to "foo" has incompatible type "int"; expected "str"'
    )
    assert diags[1].code == "arg-type"
    assert diags[1].severity == types.DiagnosticSeverity.Error


@pytest.mark.asyncio
async def test_did_open(
    server: ServerFixture, fake_document: workspace.Document
) -> None:
    params = types.DidOpenTextDocumentParams(
        text_document=types.TextDocumentItem(
            uri=fake_document.uri,
            language_id="python",
            version=1,
            text=fake_document._source,
        )
    )

    await dmypy_ls.did_open(server.server, params)
    server.fake_publish_diagnostics.assert_called_once()
    _assert_diags(server.fake_publish_diagnostics.call_args[0][1])


@pytest.mark.asyncio
async def test_did_change(
    server: ServerFixture, fake_document: workspace.Document
) -> None:
    params = types.DidChangeTextDocumentParams(
        contentChanges=[],
        text_document=types.TextDocumentItem(
            uri=fake_document.uri,
            language_id="python",
            version=1,
            text=fake_document._source,
        ),
    )
    await dmypy_ls.did_change(server.server, params)
    server.fake_publish_diagnostics.assert_called_once()
    _assert_diags(server.fake_publish_diagnostics.call_args[0][1])


    fixed_content = """
def foo(bar: str) -> int:
    return 1

foo("foo")
"""

    fixed_doc = workspace.Document(fake_document.uri, fixed_content)  # type: ignore[no-untyped-call]
    server.server.lsp.workspace.get_document = mock.Mock(return_value=fixed_doc)  # type: ignore[assignment]

    params = types.DidChangeTextDocumentParams(
        contentChanges=[],
        text_document=types.TextDocumentItem(
            uri=fixed_doc.uri,
            language_id="python",
            version=1,
            text=fixed_doc._source,
        ),
    )
    server.fake_publish_diagnostics.reset_mock()
    await dmypy_ls.did_change(server.server, params)
    server.fake_publish_diagnostics.assert_called_once()
    assert len(server.fake_publish_diagnostics.call_args[0][1]) == 0
