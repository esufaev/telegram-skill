"""Who the account is needs no grant; who is on the other side needs read on that chat.

The account's own profile is the user's, so it is always theirs to see. A chat's other
party is somebody else, so describing them takes the grant that reading their messages
takes. The description holds what the account itself can see of them: the phone number
when they share it, when they were last seen, and their bio.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from telethon.tl import types as t

from tests.fake_telethon import Channel, Chat, FakeClient, User
from tg_agentd import client as client_module
from tg_agentd import handler, permissions

SEEN = datetime(2026, 10, 21, 18, 30, tzinfo=timezone.utc)


class _Status:
    def __init__(self, offline):
        self.offline = offline


@pytest.fixture
def served(tmp_path, monkeypatch):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _Status)
    fake = FakeClient(
        premium=True,
        entities={
            777: User(
                id=777,
                first_name="Ann",
                last_name="Lee",
                username="ann",
                premium=True,
                phone="70001112233",
                status=t.UserStatusOffline(was_online=SEEN),
                about="Studies ML",
            ),
            778: User(
                id=778,
                first_name="Helper",
                username="help_bot",
                bot=True,
                phone=None,
                status=None,
                about=None,
            ),
            -5: Chat(id=5, title="Study group", participants_count=4, about="Homework"),
            -1009: Channel(id=9, title="News", username="news", about=None),
            -1010: Channel(id=10, title="Club", megagroup=True, participants_count=120),
        },
    )
    service = handler.Handler(permissions.Store(tmp_path), client_module.Client(fake))

    def ask(request, grant=None):
        if grant:
            chat, words = grant
            (tmp_path / "chats" / f"{chat}.conf").write_text(f"allow: {words}\n")
        return asyncio.run(service.handle(request))

    return ask, fake


def test_whoami_needs_no_grant_and_names_the_account(served):
    ask, _ = served
    answer = ask({"action": "whoami"})
    assert answer["ok"] is True, answer
    assert answer["result"] == {
        "id": 1,
        "type": "user",
        "name": "Self",
        "username": "self_name",
        "premium": True,
    }


def test_chat_info_is_refused_without_read_and_touches_nothing(served):
    ask, fake = served
    answer = ask({"action": "chat-info", "chat": 777}, grant=(777, "send, reply"))
    assert answer["ok"] is False
    assert "read" in answer["remedy"]
    assert not fake.called("get_entity")


@pytest.mark.parametrize("action", ["chat-info", "topics"])
def test_a_refusal_names_the_grant_rather_than_the_action(served, action):
    # Neither action is a word a permission file accepts, so a remedy naming one would
    # send the user to write a line the service then refuses
    ask, _ = served
    answer = ask({"action": action, "chat": 777}, grant=(777, "send"))
    assert answer["remedy"] == "add read to 'allow: ' in the permission file for chat 777"


def test_chat_info_describes_a_person_as_the_account_sees_them(served):
    ask, _ = served
    answer = ask({"action": "chat-info", "chat": 777}, grant=(777, "read"))
    assert answer["ok"] is True, answer
    assert answer["allowed_by"] == "read"
    assert answer["result"] == {
        "id": 777,
        "type": "user",
        "name": "Ann Lee",
        "username": "ann",
        "premium": True,
        "phone": "+70001112233",
        "seen": "2026-10-21T18:30:00+00:00",
        "about": "Studies ML",
    }


@pytest.mark.parametrize(
    "status, seen",
    [
        (t.UserStatusOnline(expires=SEEN), "online"),
        (t.UserStatusRecently(), "recently"),
        (t.UserStatusLastWeek(), "within a week"),
        (t.UserStatusLastMonth(), "within a month"),
    ],
)
def test_a_hidden_last_seen_reads_as_the_range_telegram_gives(served, status, seen):
    ask, fake = served
    fake.entities[777].status = status
    result = ask({"action": "chat-info", "chat": 777}, grant=(777, "read"))["result"]
    assert result["seen"] == seen


def test_what_a_person_does_not_share_is_left_out(served):
    ask, _ = served
    result = ask({"action": "chat-info", "chat": 778}, grant=(778, "read"))["result"]
    assert result["type"] == "bot"
    for absent in ("phone", "seen", "about"):
        assert absent not in result


@pytest.mark.parametrize(
    "chat, kind, title, members, about",
    [
        (-5, "group", "Study group", 4, "Homework"),
        (-1009, "channel", "News", None, None),
        (-1010, "supergroup", "Club", 120, None),
    ],
)
def test_a_group_or_channel_is_described_by_its_kind_and_size(
    served, chat, kind, title, members, about
):
    ask, _ = served
    result = ask({"action": "chat-info", "chat": chat}, grant=(chat, "read"))["result"]
    assert result["type"] == kind
    assert result["title"] == title
    assert result.get("members") == members
    assert result.get("about") == about
