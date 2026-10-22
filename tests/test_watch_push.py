"""The watcher takes pushed messages when the service offers them, and polls otherwise.

A subscription is opened before the catch-up read, so a message that lands during the
read arrives twice and is shown once. A service too old to know subscribe answers that
it has no such action, and the watcher polls as before. A hook's single run and a forum
topic keep polling: a run that exits at once has nothing to wait on, and a subscription
covers the whole chat rather than one topic.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("tg_watch_push", ROOT / "tg-watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
tg = watch.tg


class Options:
    def __init__(self, **fields):
        self.chat = "777"
        self.topic = None
        self.socket = "/nowhere"
        self.since = None
        self.interval = 1
        self.once = False
        self.state = None
        self.exec = None
        for name, value in fields.items():
            setattr(self, name, value)


def message(number, text, out=False):
    return {"id": number, "from": 1 if out else 42, "text": text, "out": out}


def serve(monkeypatch, history, pushed, ack=None):
    """Reads answer from `history`; a subscription acknowledges, then pushes `pushed`."""
    opened = []

    def ask(socket, request):
        since = request.get("since")
        return {"ok": True, "result": [m for m in history if since is None or m["id"] > since]}

    def stream(socket, request):
        opened.append(request)
        yield ack or {"ok": True, "action": "subscribe", "result": {"chat": 777}}
        for answer in pushed:
            yield answer

    monkeypatch.setattr(tg, "ask", ask)
    monkeypatch.setattr(tg, "stream", stream)
    return opened


def test_a_pushed_message_is_shown_once_even_when_the_catch_up_read_saw_it(
    monkeypatch, capsys
):
    serve(
        monkeypatch,
        history=[message(21, "during the read")],
        pushed=[{"ok": True, "message": message(21, "during the read")},
                {"ok": True, "message": message(22, "pushed")}],
    )
    mark = watch.follow(Options(), since=20)
    assert capsys.readouterr().out.splitlines() == [
        "21 42 during the read",
        "22 42 pushed",
    ]
    assert mark == 22


def test_the_subscription_opens_before_the_catch_up_read(monkeypatch):
    order = []
    serve(monkeypatch, history=[], pushed=[])
    stream, ask = tg.stream, tg.ask
    monkeypatch.setattr(tg, "stream", lambda s, r: (order.append("subscribe"), stream(s, r))[1])
    monkeypatch.setattr(tg, "ask", lambda s, r: (order.append("read"), ask(s, r))[1])
    watch.follow(Options(), since=20)
    assert order[:2] == ["subscribe", "read"]


def test_the_agents_own_pushed_send_is_not_echoed(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TG_SENT_LOG", str(tmp_path / "sent"))
    (tmp_path / "sent").write_text("777 23\n")
    serve(
        monkeypatch,
        history=[],
        pushed=[{"ok": True, "message": message(23, "mine", out=True)},
                {"ok": True, "message": message(24, "from the phone", out=True)}],
    )
    watch.follow(Options(), since=22)
    assert capsys.readouterr().out.splitlines() == ["24 self from the phone"]


def test_each_pushed_message_moves_the_kept_mark(monkeypatch, tmp_path):
    state = tmp_path / "mark"
    serve(monkeypatch, history=[], pushed=[{"ok": True, "message": message(31, "x")}])
    watch.follow(Options(state=str(state)), since=30)
    assert state.read_text().strip() == "31"


def test_a_refusal_in_the_stream_is_reported_and_ends_the_follow(monkeypatch, capsys):
    serve(
        monkeypatch,
        history=[],
        pushed=[{"ok": False, "error": "chat 777 does not allow read"},
                {"ok": True, "message": message(41, "never shown")}],
    )
    mark = watch.follow(Options(), since=40)
    out = capsys.readouterr().out
    assert "refused" in out and "never shown" not in out
    assert mark == 40


def test_a_service_without_subscribe_is_polled_instead(monkeypatch):
    serve(
        monkeypatch,
        history=[],
        pushed=[],
        ack={"ok": False, "error": "no action is called 'subscribe'"},
    )
    try:
        watch.follow(Options(), since=1)
    except watch.NoPush:
        return
    raise AssertionError("an old service was taken for a subscription")


def test_an_unreachable_service_is_waited_out_rather_than_ending_the_watch(
    monkeypatch, capsys
):
    def refused(socket, request):
        raise ConnectionRefusedError("Connection refused")
        yield

    monkeypatch.setattr(tg, "stream", refused)
    assert watch.step(Options(), since=60, push=True) == (60, True)
    assert "Connection refused" in capsys.readouterr().err


def test_an_old_service_switches_the_watch_to_polling(monkeypatch, capsys):
    serve(
        monkeypatch,
        history=[message(71, "polled")],
        pushed=[],
        ack={"ok": False, "error": "no action is called 'subscribe'"},
    )
    assert watch.step(Options(), since=70, push=True) == (71, False)
    assert capsys.readouterr().out.splitlines() == ["71 42 polled"]


def test_a_single_run_and_a_topic_never_subscribe(monkeypatch, capsys):
    opened = serve(monkeypatch, history=[message(51, "x")], pushed=[])
    watch.main(["--chat", "777", "--once", "--since", "50"])
    watch.main(["--chat", "777", "--topic", "9", "--once", "--since", "50"])
    assert opened == []
    assert watch.wants_push(Options(topic=9)) is False
    assert watch.wants_push(Options()) is True
