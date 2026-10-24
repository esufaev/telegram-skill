"""The login says where Telegram sent the code, and a flood wait names its seconds."""

import asyncio

import pytest
from telethon import errors

from tg_agentd import __main__ as entry


class SentCodeTypeApp:
    def __init__(self, length=5):
        self.length = length


class SentCodeTypeSms:
    def __init__(self, length=5):
        self.length = length


class SentCode:
    def __init__(self, kind):
        self.type = kind


class QR:
    """Stands in for Telethon's QRLogin: one URL, and the answers wait() gives in turn."""

    def __init__(self, answers):
        self.url = "tg://login?token=test"
        self._answers = list(answers)
        self.recreated = 0

    async def wait(self):
        answer = self._answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def recreate(self):
        self.recreated += 1


class FakeLogin:
    """The parts of TelegramClient the login flow uses, and nothing else."""

    def __init__(self, *, sent=None, sign_in_error=None, authorized=False, qr=None):
        self.sent = sent
        self.sign_in_error = sign_in_error
        self.authorized = authorized
        self.qr = qr
        self.calls = []

    async def connect(self):
        self.calls.append(("connect",))

    async def is_user_authorized(self):
        return self.authorized

    async def send_code_request(self, phone):
        self.calls.append(("send_code_request", phone))
        return self.sent

    async def sign_in(self, phone=None, code=None, password=None):
        self.calls.append(("sign_in", phone, code, password))
        if self.sign_in_error is not None:
            error, self.sign_in_error = self.sign_in_error, None
            raise error
        return object()

    async def qr_login(self):
        return self.qr


def answers(monkeypatch, *values):
    queued = iter(values)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(queued))


def test_the_code_flow_names_the_channel_and_its_length(monkeypatch, capsys):
    fake = FakeLogin(sent=SentCode(SentCodeTypeSms(length=5)))
    answers(monkeypatch, "+1 555 0100", "12345")

    asyncio.run(entry.login(fake, by_qr=False))

    assert "by SMS (5 digits)" in capsys.readouterr().out
    assert ("sign_in", "+1 555 0100", "12345", None) in fake.calls


def test_a_code_through_the_app_says_which_chat_to_open(monkeypatch, capsys):
    fake = FakeLogin(sent=SentCode(SentCodeTypeApp(length=5)))
    answers(monkeypatch, "+1 555 0100", "12345")

    asyncio.run(entry.login(fake, by_qr=False))

    assert "chat named Telegram" in capsys.readouterr().out


def test_a_flood_wait_names_the_seconds(monkeypatch):
    class Refusing(FakeLogin):
        async def send_code_request(self, phone):
            raise errors.FloodWaitError(request=None, capture=42)

    answers(monkeypatch, "+1 555 0100")

    with pytest.raises(SystemExit) as stop:
        asyncio.run(entry.login(Refusing(), by_qr=False))

    assert "42 seconds" in str(stop.value)


def test_two_factor_asks_for_the_password(monkeypatch):
    fake = FakeLogin(
        sent=SentCode(SentCodeTypeSms()),
        sign_in_error=errors.SessionPasswordNeededError(None),
    )
    answers(monkeypatch, "+1 555 0100", "12345")
    monkeypatch.setattr(entry.getpass, "getpass", lambda _prompt: "hunter2")

    asyncio.run(entry.login(fake, by_qr=False))

    assert ("sign_in", None, None, "hunter2") in fake.calls


def test_the_qr_flow_prints_the_link_a_device_can_scan(capsys):
    user = object()
    fake = FakeLogin(qr=QR([user]))

    signed = asyncio.run(entry.login(fake, by_qr=True))

    assert "tg://login?token=test" in capsys.readouterr().out
    assert signed is user


def test_an_expired_qr_is_shown_again(capsys):
    user = object()
    qr = QR([asyncio.TimeoutError(), user])
    fake = FakeLogin(qr=qr)

    signed = asyncio.run(entry.login(fake, by_qr=True))

    assert qr.recreated == 1
    assert "expired" in capsys.readouterr().out
    assert signed is user


def test_two_factor_after_a_qr_asks_for_the_password(monkeypatch):
    fake = FakeLogin(
        qr=QR([errors.SessionPasswordNeededError(None)]),
        sign_in_error=None,
    )
    monkeypatch.setattr(entry.getpass, "getpass", lambda _prompt: "hunter2")

    asyncio.run(entry.login(fake, by_qr=True))

    assert ("sign_in", None, None, "hunter2") in fake.calls


def test_an_authorized_session_asks_for_nothing(capsys):
    fake = FakeLogin(authorized=True)

    asyncio.run(entry.login(fake, by_qr=False))

    assert fake.calls == [("connect",)]
    assert capsys.readouterr().out == ""


def test_the_qr_flag_needs_login():
    with pytest.raises(SystemExit):
        entry.main(["--permissions", "permissions", "--qr"])
