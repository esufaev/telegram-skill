"""A file goes out under the grant that sends words, and only its bytes cross the socket.

The service never opens a path for an upload. The client reads the file with the caller's
own access and puts the bytes into the request, so the service cannot be asked to send
what only it can read, such as its own session.
"""

import asyncio
import base64
import importlib.util
import json
import socket
from pathlib import Path

import pytest

from tests.fake_telethon import FakeClient, Message
from tg_agentd import client as client_module
from tg_agentd import handler, permissions, server

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("tg_client", ROOT / "tg.py")
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    return permissions.Store(tmp_path)


@pytest.fixture
def fake():
    return FakeClient(messages={777: [Message(id=1, text="hi", sender_id=42)]})


@pytest.fixture
def ask(store, fake, monkeypatch):
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    return call


def grant(store, chat, words):
    (store.root / "chats" / f"{chat}.conf").write_text(f"allow: {words}\n")


def attached(name, data):
    return {"name": name, "data": base64.b64encode(data).decode()}


def test_send_with_a_file_uploads_its_bytes_under_its_name(ask, store, fake):
    grant(store, 777, "send")
    answer = ask(
        {
            "action": "send",
            "chat": 777,
            "text": "the report",
            "file": attached("report.pdf", b"%PDF-1.7 body"),
        }
    )
    assert answer["ok"] is True, answer
    call = fake.called("send_file")[-1]
    assert call["file"].read() == b"%PDF-1.7 body"
    assert call["file"].name == "report.pdf"
    assert call["caption"] == "the report"
    assert not fake.called("send_message")


def test_reply_with_a_file_keeps_the_thread(ask, store, fake):
    grant(store, 777, "reply")
    answer = ask(
        {
            "action": "reply",
            "chat": 777,
            "reply_to": 1,
            "file": attached("a.txt", b"x"),
        }
    )
    assert answer["ok"] is True, answer
    assert fake.called("send_file")[-1]["reply_to"] == 1


def test_a_file_needs_the_same_grant_as_words(ask, store, fake):
    grant(store, 777, "read")
    answer = ask(
        {"action": "send", "chat": 777, "file": attached("a.txt", b"x")}
    )
    assert answer["ok"] is False
    assert not fake.called("send_file")


def test_a_file_that_is_not_base64_is_refused_before_the_account(ask, store, fake):
    grant(store, 777, "send")
    answer = ask(
        {"action": "send", "chat": 777, "file": {"name": "a.txt", "data": "%%%"}}
    )
    assert answer["ok"] is False
    assert "base64" in answer["error"]
    assert not fake.called("send_file")


def test_a_file_name_cannot_carry_a_path(ask, store, fake):
    grant(store, 777, "send")
    ask({"action": "send", "chat": 777, "file": attached("../../etc/passwd", b"x")})
    assert "/" not in fake.called("send_file")[-1]["file"].name


def test_the_client_reads_the_file_and_names_it_by_its_basename(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"\x00\x01binary")
    options = tg.build_parser().parse_args(
        ["send", "--chat", "777", "--file", str(path)]
    )
    request = tg.request_from(options)
    assert request["file"]["name"] == "report.pdf"
    assert base64.b64decode(request["file"]["data"]) == b"\x00\x01binary"


class _Echo:
    async def handle(self, request):
        return {"ok": True, "size": len(request.get("file", {}).get("data", ""))}


class _Broken:
    async def handle(self, request):
        raise ValueError("the handler broke")


def _serve_once(tmp_path, payload):
    """Send one line to a real Server over a unix socket and return its answer."""
    path = str(tmp_path / "s")

    async def scenario():
        served = asyncio.ensure_future(server.Server(_Echo()).serve_forever(path=path))
        while not Path(path).exists():
            await asyncio.sleep(0.01)
        answer = await asyncio.to_thread(_exchange, path, payload)
        served.cancel()
        return answer

    return asyncio.run(scenario())


def _exchange(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(path)
        try:
            connection.sendall(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass
        answer = b""
        while not answer.endswith(b"\n"):
            block = connection.recv(65536)
            if not block:
                break
            answer += block
    return json.loads(answer)


def test_a_request_far_longer_than_the_stream_default_is_served(tmp_path):
    data = "A" * (4 * 1024 * 1024)
    line = json.dumps({"action": "send", "file": {"name": "a", "data": data}})
    answer = _serve_once(tmp_path, line.encode() + b"\n")
    assert answer == {"ok": True, "size": len(data)}


def test_a_request_over_the_limit_gets_an_answer_rather_than_a_closed_socket(tmp_path):
    line = b'{"data": "' + b"A" * (server.MAX_REQUEST + 1) + b'"}\n'
    answer = _serve_once(tmp_path, line)
    assert answer["ok"] is False
    assert "too large" in answer["error"]


def _ask_and_leave(path, payload):
    """Send one request and close without reading the answer.

    A threaded call, because a blocking sendall on the loop's own thread would stop
    the server from reading — the same reason `_serve_once` serves from a thread.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(path)
        try:
            client.sendall(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass
        client.close()


def _leave_without_the_answer(tmp_path, payload):
    """Ask a real Server, close without reading the answer, then ask once more.

    The loop's own exception handler is replaced for one run, so anything the session
    emits out of turn is collected and handed back instead of reaching a journal.
    """
    path = str(tmp_path / "s")

    async def scenario():
        seen = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _, context: seen.append(context))
        served = asyncio.ensure_future(server.Server(_Echo()).serve_forever(path=path))
        while not Path(path).exists():
            await asyncio.sleep(0.01)
        await asyncio.to_thread(_ask_and_leave, path, payload)
        for _ in range(40):
            if any("exception" in context for context in seen):
                break
            await asyncio.sleep(0.01)
        answer = await asyncio.to_thread(_exchange, path, payload)
        served.cancel()
        return seen, answer

    return asyncio.run(scenario())


def test_a_client_that_leaves_before_the_answer_ends_the_session_without_an_exception(tmp_path):
    payload = b'{"the": "request"}\n'
    seen, answer = _leave_without_the_answer(tmp_path, payload)
    assert answer == {"ok": True, "size": 0}
    assert [context for context in seen if context.get("exception") is not None] == []


def test_a_rejection_to_a_client_that_left_ends_the_session_without_an_exception(tmp_path):
    payload = b'{"data": "' + b"A" * (server.MAX_REQUEST + 1) + b'"}\n'
    seen, answer = _leave_without_the_answer(tmp_path, payload)
    assert answer["ok"] is False
    assert "too large" in answer["error"]
    assert [context for context in seen if context.get("exception") is not None] == []


def _read_until_eof(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(path)
        client.sendall(payload)
        answer = b""
        while True:
            block = client.recv(65536)
            if not block:
                return answer
            answer += block


def test_a_fault_in_the_handler_is_not_answered_as_an_oversized_request(tmp_path):
    path = str(tmp_path / "s")

    async def scenario():
        seen = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _, context: seen.append(context))
        served = asyncio.ensure_future(server.Server(_Broken()).serve_forever(path=path))
        while not Path(path).exists():
            await asyncio.sleep(0.01)
        answer = await asyncio.to_thread(_read_until_eof, path, b'{"the": "request"}\n')
        for _ in range(40):
            if any("exception" in context for context in seen):
                break
            await asyncio.sleep(0.01)
        served.cancel()
        return seen, answer

    seen, answer = asyncio.run(scenario())
    assert b"too large" not in answer
    faults = [
        context["exception"] for context in seen if context.get("exception") is not None
    ]
    assert any(isinstance(fault, ValueError) for fault in faults)
