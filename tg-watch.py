#!/usr/bin/env python3
"""Follow a chat and print each new message as one line.

Every line is an event: the identifier, who sent it, and the text with newlines folded
away, so a reader that consumes lines sees one message per line. The service pushes each
message as it lands; where it cannot, the chat is polled. Either way the identifier of
the last message seen is what a read asks from, rather than a count of recent ones —
asking for the last N loses whatever arrived between two rounds and repeats what did
not.

Both sides are followed. A message the account itself sent from a phone is as much an
event as one that arrived, and a watcher that hides it shows half a conversation. What
tg.py sent is the exception: the agent wrote it and already knows it, so it is left out.

A hook starts the watcher fresh on every run, so the mark it resumes from can live in a
file, and a command can be run for each new message instead of reading the lines.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tg  # noqa: E402  — the client beside this file, which owns talking to the socket

DEFAULT_INTERVAL = 20


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tg-watch.py",
        description="Print each new message in a chat as it arrives, one line each",
        epilog=(
            "The chat needs read permission, like any other reading.\n\n"
            "Examples:\n"
            "  tg-watch.py --chat 932504634\n"
            "  tg-watch.py --chat -1001804413056 --topic 43628 --interval 60\n"
            "  tg-watch.py --chat 932504634 --once --state ~/.local/state/tg-agent/932504634\n"
            "  tg-watch.py --chat 932504634 --exec 'notify-send \"$(jq -r .text)\"'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--chat", required=True, help="the chat to follow")
    parser.add_argument("--topic", type=int, help="one topic of a forum, if it is one")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help="seconds between polls, where the chat is polled, and before a broken "
        "stream is opened again (default: %(default)s)",
    )
    parser.add_argument(
        "--since",
        type=int,
        help="start from this message instead of from the latest one",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="print what is new and exit, instead of following",
    )
    parser.add_argument(
        "--state",
        help="a file holding the last message seen, read at start and written after "
        "each round, so a watcher started again by a hook resumes where it stopped",
    )
    parser.add_argument(
        "--exec",
        help="a shell command run for each new message, with the message as one JSON "
        "line on its standard input; the lines are still printed",
    )
    parser.add_argument(
        "--socket",
        default=tg.DEFAULT_SOCKET,
        help="the service socket (default: $TG_SOCKET, else %(default)s)",
    )
    return parser


def line_for(message):
    """One event as one line: nothing in it may contain a newline."""
    who = "self" if message.get("out") else str(message.get("from"))
    kind = message.get("media")
    text = (message.get("text") or "").replace("\n", " ")
    if kind:
        text = f"[{kind}]" + (f" {text}" if text else "")
    text = text or "[empty]"
    return f"{message['id']} {who} {text}"


def newest(messages):
    """The highest identifier in a batch, or None when it is empty."""
    return max((m["id"] for m in messages), default=None)


def request_for(options, since):
    request = {"action": "read", "chat": options.chat, "limit": 50}
    if since is not None:
        request["since"] = since
    if options.topic:
        request["topic"] = options.topic
    return request


def latest_id(options):
    """Where to start when the caller named no starting point."""
    answer = tg.ask(options.socket, request_for(options, None) | {"limit": 1})
    if not answer.get("ok"):
        raise SystemExit(f"tg-watch.py: {answer.get('error')}")
    return newest(answer.get("result") or [])


def round_once(options, since):
    """Print what is newer than `since`, and answer with the new starting point.

    A refusal is printed and the starting point kept: a permission withdrawn while this
    runs should stop the output, not the watch, because the user may put it back.
    """
    answer = tg.ask(options.socket, request_for(options, since))
    if not answer.get("ok"):
        print(f"refused: {answer.get('error')}", flush=True)
        return since
    messages = sorted(answer.get("result") or [], key=lambda m: m["id"])
    for message in messages:
        since = shown(options, message, since)
    return since


class NoPush(Exception):
    """The service has no subscribe action, so the chat has to be polled."""


def wants_push(options):
    """A subscription covers a whole chat, so one topic of a forum is polled instead."""
    return not options.topic


def follow(options, since):
    """Subscribe, catch up by reading, then show each pushed message as it arrives.

    The subscription is opened before the catch-up read, so a message that lands during
    the read is pushed as well, and the mark keeps it from being shown twice. Answers
    with the mark to resume from once the stream ends.
    """
    answers = tg.stream(options.socket, {"action": "subscribe", "chat": options.chat})
    first = next(answers, None)
    if first is None:
        return since
    if not first.get("ok"):
        if str(first.get("error", "")).startswith("no action is called"):
            raise NoPush
        print(f"refused: {first.get('error')}", flush=True)
        return since
    since = round_once(options, since)
    for answer in answers:
        if not answer.get("ok"):
            print(f"refused: {answer.get('error')}", flush=True)
            return since
        since = shown(options, answer["message"], since)
    return since


def shown(options, message, since):
    """Show one message unless it is older than the mark or the agent sent it.

    Answers with the mark after it, which is kept in the state file at once, so a
    watcher stopped mid-stream resumes after the last message it handled.
    """
    if since is not None and message["id"] <= since:
        return since
    if not (message.get("out") and message["id"] in tg.sent_ids(options.chat)):
        print(line_for(message), flush=True)
        if options.exec:
            run_for(options, message)
    if options.state:
        write_mark(options.state, message["id"])
    return message["id"]


def step(options, since, push):
    """One turn of the watch: a subscription while the service offers one, else a poll.

    A service that cannot be reached is reported and waited out rather than ending the
    watch, since it restarts in seconds whenever its system is rebuilt.
    """
    try:
        if push:
            try:
                return follow(options, since), True
            except NoPush:
                return round_once(options, since), False
        return round_once(options, since), False
    except OSError as unreachable:
        print(f"tg-watch.py: {options.socket}: {unreachable}", file=sys.stderr)
        return since, push


def run_for(options, message):
    """Run the caller's command on one message. Its failure is reported, not fatal."""
    event = json.dumps({"chat": options.chat, **message}, ensure_ascii=False)
    status = subprocess.run(
        options.exec, shell=True, input=event + "\n", text=True
    ).returncode
    if status:
        print(f"tg-watch.py: --exec exited {status} on {message['id']}", file=sys.stderr)


def read_mark(path):
    try:
        with open(path, encoding="utf-8") as kept:
            return int(kept.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_mark(path, mark):
    if mark is None:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(f"{mark}\n", encoding="utf-8")


def main(argv=None):
    options = build_parser().parse_args(argv)
    try:
        since = options.since
        if since is None and options.state:
            since = read_mark(options.state)
        if since is None:
            since = latest_id(options)
        if options.once:
            since = round_once(options, since)
            if options.state:
                write_mark(options.state, since)
            return 0
        push = wants_push(options)
        while True:
            since, push = step(options, since, push)
            if options.state:
                write_mark(options.state, since)
            time.sleep(options.interval)
    except KeyboardInterrupt:
        return 0
    except (OSError, json.JSONDecodeError) as unreachable:
        print(f"tg-watch.py: {options.socket}: {unreachable}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
