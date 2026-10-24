"""Listing every chat spans the account, so only the default chat file grants it."""

import asyncio

import pytest

from tests.fake_telethon import Dialog, FakeClient
from tg_agentd import client as client_module
from tg_agentd import handler, permissions


class _FakeStatusRequest:
    def __init__(self, offline):
        self.offline = offline


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    return permissions.Store(tmp_path)


@pytest.fixture
def ask(store, monkeypatch):
    fake = FakeClient(
        dialogs=[
            Dialog(id=777, name="Anna", unread_count=2),
            Dialog(id=-1001234567890, name="Team", unread_count=0),
        ],
        archived_dialogs=[Dialog(id=888, name="Old", unread_count=1)],
    )
    monkeypatch.setattr(client_module, "UpdateStatusRequest", _FakeStatusRequest)
    served = handler.Handler(store, client_module.Client(fake))

    def call(request):
        return asyncio.run(served.handle(request))

    call.fake = fake
    return call


def grant_default(store, words):
    (store.root / "chats" / f"{permissions.DEFAULT}.conf").write_text(f"allow: {words}\n")


def test_without_the_default_grant_the_list_is_refused(ask, store):
    answer = ask({"action": "dialogs"})
    assert answer["ok"] is False
    assert "chats/default.conf" in answer["remedy"]


def test_the_list_holds_every_chat_with_its_name_and_unread_count(ask, store):
    grant_default(store, "dialogs")
    answer = ask({"action": "dialogs"})
    assert answer["ok"] is True
    chats = {entry["chat"]: entry for entry in answer["result"]["chats"]}
    assert chats[777]["name"] == "Anna"
    assert chats[777]["unread"] == 2
    assert chats[-1001234567890]["name"] == "Team"
    assert chats[888]["archived"] is True
    assert chats[888]["unread"] == 1


def test_the_list_marks_nothing_read(ask, store):
    grant_default(store, "dialogs")
    ask({"action": "dialogs"})
    assert not ask.fake.called("send_read_acknowledge")


def test_the_wildcard_alone_does_not_open_the_account_list(ask, store):
    grant_default(store, "all")
    assert ask({"action": "dialogs"})["ok"] is False
