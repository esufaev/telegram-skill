"""Hand each new message to whoever subscribed to its source.

The service receives every new message for every source, whether anyone is listening or
not. The hub keeps a bounded queue per subscriber, so a reader that stops reading costs a
fixed amount of memory and is then cut off: it can catch up by reading history from the
last message it saw, which the service cannot do on its behalf.
"""

import asyncio
import collections

# How many undelivered messages a subscriber may owe before it is cut off
DEPTH = 256


class FellBehind(Exception):
    """The subscriber stopped reading for longer than its queue holds."""


class Subscription:
    """One subscriber's queue of messages from one source."""

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
    """Subscriptions by source, keyed the way the permission files name it."""

    def __init__(self):
        self.depth = DEPTH
        self._by_key = collections.defaultdict(set)

    def subscribe(self, key):
        subscription = Subscription(self.depth)
        self._by_key[key].add(subscription)
        return subscription

    def unsubscribe(self, key, subscription):
        self._by_key[key].discard(subscription)
        if not self._by_key[key]:
            del self._by_key[key]

    def publish(self, key, message):
        for subscription in list(self._by_key.get(key, ())):
            subscription.offer(message)

    def count(self, key):
        return len(self._by_key.get(key, ()))
