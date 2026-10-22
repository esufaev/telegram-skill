"""Describe a user, group or channel in the fields an agent can act on.

A field the account cannot see, such as a phone number a person hides, is left out
rather than set to null, so an answer never suggests a value that exists but is empty.
"""

# What Telegram shows in place of an exact time when a person hides when they were seen
SEEN_RANGES = {
    "UserStatusOnline": "online",
    "UserStatusRecently": "recently",
    "UserStatusLastWeek": "within a week",
    "UserStatusLastMonth": "within a month",
}


def describe(entity, full=None, private=True):
    """The entity as a flat dictionary.

    `full` is the full profile, which holds the bio and a group's size. `private` adds
    the phone number and the last-seen status, which say nothing about the account's own
    profile to the account itself.
    """
    kind = type(entity).__name__
    if kind == "User":
        described = {
            "id": entity.id,
            "type": "bot" if getattr(entity, "bot", False) else "user",
            "name": " ".join(
                part for part in (entity.first_name, entity.last_name) if part
            ),
            "username": entity.username,
            "premium": bool(getattr(entity, "premium", False)),
        }
        if private:
            described["phone"] = f"+{entity.phone}" if entity.phone else None
            described["seen"] = _seen(getattr(entity, "status", None))
    else:
        if kind == "Chat":
            sort = "group"
        elif getattr(entity, "megagroup", False):
            sort = "supergroup"
        else:
            sort = "channel"
        described = {
            "id": entity.id,
            "type": sort,
            "title": getattr(entity, "title", None),
            "username": getattr(entity, "username", None),
            "members": getattr(full, "participants_count", None)
            or getattr(entity, "participants_count", None),
        }
    described["about"] = getattr(full, "about", None) or None
    return {key: value for key, value in described.items() if value is not None}


def _seen(status):
    if status is None:
        return None
    name = type(status).__name__
    if name == "UserStatusOffline":
        return status.was_online.isoformat()
    return SEEN_RANGES.get(name)
