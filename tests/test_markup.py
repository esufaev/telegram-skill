"""Text goes out as Markdown unless the request names another markup, and the mode is stated.

Telethon parses text on the client and sends the server only the result, so the markup is
a choice this service makes on every write; leaving it out would hand it to whatever
default Telethon has. A custom emoji is the one piece of markup the server keeps from an
account without Premium, so the service refuses it first, with an answer that says why,
rather than letting the emoji vanish. Rich markup is the exception to client-side
parsing: the server parses it, so the text crosses the wire as written.
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from tests.fake_telethon import OMITTED, FakeClient, Message
from tg_agentd import client as client_module
from tg_agentd import handler, permissions

EMOJI = '<tg-emoji emoji-id="5368324170671202286">👍</tg-emoji>'

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("tg_client", ROOT / "tg.py")
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


def serve(tmp_path, monkeypatch, premium=False):
    (tmp_path / "chats").mkdir(exist_ok=True)
    (tmp_path / "folders").mkdir(exist_ok=True)
    (tmp_path / "chats" / "777.conf").write_text("allow: send, reply, edit\n")
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    fake = FakeClient(
        messages={777: [Message(id=1, text="hi", sender_id=42)]}, premium=premium
    )
    served = handler.Handler(permissions.Store(tmp_path), client_module.Client(fake))

    def ask(request):
        return asyncio.run(served.handle(request))

    return ask, fake


@pytest.fixture
def ask(tmp_path, monkeypatch):
    return serve(tmp_path, monkeypatch)


def test_text_goes_out_as_markdown_when_no_markup_is_named(ask):
    call, fake = ask
    answer = call({"action": "send", "chat": 777, "text": "**bold**"})
    assert answer["ok"] is True
    assert fake.called("send_message")[-1]["parse_mode"] == "md"


@pytest.mark.parametrize("named, passed", [("html", "html"), ("md", "md"), ("none", None)])
def test_the_named_markup_reaches_telethon_explicitly(ask, named, passed):
    # "none" has to arrive as None. Leaving the keyword out would let Telethon parse the
    # text as Markdown, which is the opposite of what was asked
    call, fake = ask
    call({"action": "send", "chat": 777, "text": "x", "parse_mode": named})
    assert fake.called("send_message")[-1]["parse_mode"] == passed
    assert fake.called("send_message")[-1]["parse_mode"] != OMITTED


def test_a_reply_carries_the_markup_too(ask):
    call, fake = ask
    call({"action": "reply", "chat": 777, "text": "x", "reply_to": 1, "parse_mode": "html"})
    assert fake.called("send_message")[-1]["parse_mode"] == "html"


def test_an_edit_carries_the_markup(ask):
    call, fake = ask
    call({"action": "edit", "chat": 777, "id": 1, "text": "__x__"})
    assert fake.called("edit_message")[-1]["parse_mode"] == "md"


def test_a_file_caption_carries_the_markup(ask):
    call, fake = ask
    call(
        {
            "action": "send",
            "chat": 777,
            "text": "**caption**",
            "file": {"name": "a.txt", "data": "aGk="},
        }
    )
    assert fake.called("send_file")[-1]["parse_mode"] == "md"


def test_an_unknown_markup_is_refused_and_nothing_is_sent(ask):
    call, fake = ask
    answer = call({"action": "send", "chat": 777, "text": "x", "parse_mode": "MarkdownV2"})
    assert answer["ok"] is False
    assert "MarkdownV2" in answer["error"]
    # The refusal names what is accepted, so the caller can correct the request at once
    for accepted in ("html", "md", "none", "rich"):
        assert accepted in answer["error"]
    assert not fake.called("send_message")
    assert not fake.called("raw_rich")


# Telethon's Markdown has no syntax for a custom emoji, so HTML is where one is written
def test_a_custom_emoji_without_premium_is_refused_and_nothing_is_sent(ask):
    call, fake = ask
    answer = call(
        {"action": "send", "chat": 777, "text": f"look {EMOJI}", "parse_mode": "html"}
    )
    assert answer["ok"] is False
    assert "Premium" in answer["error"]
    assert not fake.called("send_message")


def test_a_custom_emoji_with_premium_goes_out(tmp_path, monkeypatch):
    call, fake = serve(tmp_path, monkeypatch, premium=True)
    answer = call(
        {"action": "send", "chat": 777, "text": f"look {EMOJI}", "parse_mode": "html"}
    )
    assert answer["ok"] is True
    assert fake.called("send_message")


def test_the_premium_question_is_asked_only_for_a_custom_emoji(ask):
    call, fake = ask
    call({"action": "send", "chat": 777, "text": "<b>plain</b>", "parse_mode": "html"})
    assert not fake.called("get_me")


RICH = "| model | WAPE |\n|---|---|\n| LGB | **0.166** |\n\n$\\sum |y - \\hat y|$"


def test_rich_text_crosses_the_wire_as_written_for_the_server_to_parse(ask):
    call, fake = ask
    answer = call({"action": "send", "chat": 777, "text": RICH, "parse_mode": "rich"})
    assert answer["ok"] is True, answer
    sent = fake.called("raw_rich")
    assert [(s["request"], s["kind"], s["markdown"]) for s in sent] == [
        ("SendMessageRequest", "InputRichMessageMarkdown", RICH)
    ]
    assert sent[0]["peer"] == 777
    # Nothing went the client-parsed way beside it
    assert not fake.called("send_message")


def test_a_rich_reply_keeps_the_thread(ask):
    call, fake = ask
    call({"action": "reply", "chat": 777, "text": RICH, "reply_to": 1, "parse_mode": "rich"})
    assert fake.called("raw_rich")[-1]["reply_to"] == 1


def test_a_rich_send_is_answered_with_the_message_it_made(ask):
    call, fake = ask
    answer = call({"action": "send", "chat": 777, "text": RICH, "parse_mode": "rich"})
    assert answer["result"]["id"] == 1001


def test_a_rich_edit_replaces_the_text_with_rich_markup(ask):
    call, fake = ask
    answer = call(
        {"action": "edit", "chat": 777, "id": 1, "text": RICH, "parse_mode": "rich"}
    )
    assert answer["ok"] is True, answer
    edited = fake.called("raw_rich")
    assert [(e["request"], e["id"], e["markdown"]) for e in edited] == [
        ("EditMessageRequest", 1, RICH)
    ]
    assert not fake.called("edit_message")


def test_rich_markup_cannot_caption_a_file(ask):
    call, fake = ask
    answer = call(
        {
            "action": "send",
            "chat": 777,
            "text": RICH,
            "parse_mode": "rich",
            "file": {"name": "a.txt", "data": "aGk="},
        }
    )
    assert answer["ok"] is False
    # The refusal says why, rather than surfacing whatever broke further down
    assert "caption" in answer["error"]
    assert not fake.called("send_file")
    assert not fake.called("raw_rich")


def test_the_client_passes_the_named_markup_on():
    options = tg.build_parser().parse_args(
        ["send", "--chat", "777", "--text", "x", "--parse-mode", "md"]
    )
    assert tg.request_from(options)["parse_mode"] == "md"


def test_the_client_leaves_the_default_to_the_service():
    # One default, kept where the decision is made
    options = tg.build_parser().parse_args(["send", "--chat", "777", "--text", "x"])
    assert "parse_mode" not in tg.request_from(options)


def test_a_tag_written_as_text_under_none_is_not_an_emoji(ask):
    # With no markup the tag is literal text, so there is nothing Premium would decide
    call, fake = ask
    answer = call({"action": "send", "chat": 777, "text": EMOJI, "parse_mode": "none"})
    assert answer["ok"] is True
    assert fake.called("send_message")
