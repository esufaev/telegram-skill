"""Hand each new message to whoever subscribed to its chat.

The session receives every update Telegram pushes, for every chat, whether anyone is
listening or not. The hub keeps a bounded queue per subscriber, so a reader that stops
reading costs a fixed amount of memory and is then cut off: it can catch up by reading
history from the last message it saw, which the service cannot do on its behalf.
"""

import asyncio
import collections

# How many undelivered messages a subscriber may owe before it is cut off
DEPTH = 256


class FellBehind(Exception):
    """The subscriber stopped reading for longer than its queue holds."""


class Subscription:
    """One subscriber's queue of messages from one chat."""

    def __init__(self, depth):
        self._depth = depth
        self._waiting = collections.deque()
        self._ready = asyncio.Event()
        self.behind = False

    def offer(self, message):
        if self.behind:
            return
        if len(self._waiting) >= self._depth:
            # What is queued is now incomplete, so delivering it would hide a gap
            self.behind = True
            self._waiting.clear()
        else:
            self._waiting.append(message)
        self._ready.set()

    async def next(self):
        while not self._waiting and not self.behind:
            self._ready.clear()
            await self._ready.wait()
        if self.behind:
            raise FellBehind
        return self._waiting.popleft()


class Hub:
    """Subscriptions by chat identifier, the marked one that permission files use."""

    def __init__(self):
        self.depth = DEPTH
        self._by_chat = collections.defaultdict(set)

    def subscribe(self, chat):
        subscription = Subscription(self.depth)
        self._by_chat[chat].add(subscription)
        return subscription

    def unsubscribe(self, chat, subscription):
        self._by_chat[chat].discard(subscription)
        if not self._by_chat[chat]:
            del self._by_chat[chat]

    def publish(self, chat, message):
        for subscription in list(self._by_chat.get(chat, ())):
            subscription.offer(message)

    def count(self, chat):
        return len(self._by_chat.get(chat, ()))
