"""Read the permission files and decide whether an action is allowed.

The files live in a directory the agent cannot write. The agent proposes an edit and the
user applies it; nothing here writes. A missing file is a refusal, not an error, so a
target nobody granted anything on behaves the same as one that does not exist — unless
`chats/default.conf` or `folders/default.conf` exists, which is a grant the user wrote
for every target without a file of its own.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import verbs

# The identifier reaches this module from the agent, and a file path is built from it.
# Telegram identifiers are integers: users positive, groups negative, channels prefixed
# with -100. Anything else is refused before it can become a path
IDENTIFIER = re.compile(r"\A-?[0-9]{1,20}\Z")

TRUE_WORDS = frozenset({"yes", "true", "on", "1"})

# The permission file that applies to every target of its kind without a file of its
# own. The name is not a Telegram identifier, so no chat or folder can shadow it
DEFAULT = "default"


class BadIdentifier(ValueError):
    """The chat or folder identifier is not a plain Telegram identifier."""


def as_identifier(value):
    """Validate an identifier and return it as a number.

    The number matters as much as the validation. Telethon resolves a string as a
    username or a phone number, never as an identifier, so a chat passed as text reaches
    the account as a name nobody has.
    """
    name = str(value)
    if not IDENTIFIER.match(name):
        raise BadIdentifier(f"{name!r} is not a Telegram identifier")
    return int(name)


class PermissionFileError(ValueError):
    """The permission file holds a word no action table defines."""


class UnknownAction(ValueError):
    """The caller asked about an action that no table defines."""


@dataclass(frozen=True)
class Decision:
    """Whether an action is allowed, and what decided it.

    `by` holds the granted word that permitted the action. It differs from the action
    itself when a wildcard permitted it, and every report says which, because a wildcard
    was one word standing for a whole table.
    """

    allowed: bool
    by: str | None
    reason: str


class Store:
    """The permission files, read fresh on every question.

    Nothing is cached. The user edits these files between requests, and a cached grant
    would outlive a permission they withdrew.
    """

    def __init__(self, root):
        self.root = Path(root)

    def chat_may(self, chat_id, action):
        """Decide whether this action is allowed on this chat."""
        if action not in verbs.CHAT_ACTIONS:
            raise UnknownAction(f"no chat action is called {action!r}")
        return self._decide("chats", chat_id, action, verbs.CHAT_ACTIONS)

    def account_may(self, action):
        """Decide an action that spans the account, by `chats/default.conf` alone.

        The wildcard never grants one of these: a user who wrote `all` chose the chat
        actions, and listing every chat is a separate decision about the whole account.
        """
        if action not in verbs.ACCOUNT_ACTIONS:
            raise UnknownAction(f"no account action is called {action!r}")
        path = self.root / "chats" / f"{DEFAULT}.conf"
        fields = self._read_fields(path)
        if fields is None:
            return Decision(False, None, f"no {path} to allow {action}")
        granted = self._words(
            fields, path, "chats", verbs.CHAT_ACTIONS | verbs.ACCOUNT_ACTIONS
        )
        if action in granted:
            return Decision(True, action, f"the default chat grant allows {action}")
        held = ", ".join(sorted(granted)) or "nothing"
        return Decision(
            False, None, f"the default chat grant does not allow {action}: holds {held}"
        )

    def folder_may(self, folder_id, action):
        """Decide whether this action is allowed on this folder.

        A folder that grants reading of its contents refuses every action that could add
        a chat to it. Without that refusal the agent widens its own reach: it puts a chat
        into a folder it may read, and reads a chat nobody granted it.
        """
        if action not in verbs.FOLDER_ACTIONS:
            raise UnknownAction(f"no folder action is called {action!r}")
        effective = self._effective("folders", folder_id)
        if effective is None:
            return self._no_file("folders", folder_id)
        fields, path, by_default = effective
        granted = self._words(fields, path, "folders", verbs.FOLDER_ACTIONS)
        if verbs.widens_reach(action) and verbs.grants_content(granted):
            return Decision(
                False,
                None,
                f"folder {folder_id} grants content, so it cannot be edited: "
                f"holds {', '.join(sorted(granted))}",
            )
        return self._as_default(
            self._weigh(granted, action, verbs.FOLDER_ACTIONS, "folder", folder_id),
            by_default,
        )

    def survey(self):
        """Every target that holds a grant, with the actions each one expands to.

        A wildcard is listed expanded rather than as one word, so that reading this
        answer shows the reach it actually carries. A file that cannot be parsed is
        reported in place of its actions, because hiding it would make a broken grant
        look like a missing one.
        """
        return {
            "chats": self._survey_kind("chats", verbs.CHAT_ACTIONS),
            "folders": self._survey_kind("folders", verbs.FOLDER_ACTIONS),
        }

    def _survey_kind(self, kind, table):
        held = {}
        directory = self.root / kind
        if not directory.is_dir():
            return held
        for path in sorted(directory.glob("*.conf")):
            name = path.stem
            try:
                if name == DEFAULT:
                    words_table = table | verbs.ACCOUNT_ACTIONS if kind == "chats" else table
                    words = self._words(
                        self._read_fields(path) or {}, path, kind, words_table
                    )
                else:
                    words = self._granted_words(kind, name, table) or set()
            except (PermissionFileError, BadIdentifier) as broken:
                held[name] = [str(broken)]
                continue
            actions = set()
            for word in words:
                if word in verbs.ACCOUNT_ACTIONS:
                    actions.add(word)
                else:
                    actions.update(verbs.expand(word, table))
            held[name] = sorted(actions)
        return held

    def chat_flag(self, chat_id, name):
        """Read a boolean setting from a chat's permission file, false when absent."""
        fields = self._fields("chats", chat_id)
        if fields is None:
            return False
        return fields.get(name, "").strip().lower() in TRUE_WORDS

    def folder_field(self, folder_id, name):
        """Read a plain setting from a folder's permission file, empty when absent."""
        fields = self._fields("folders", folder_id)
        if fields is None:
            return ""
        return fields.get(name, "").strip()

    def _decide(self, kind, identifier, action, table):
        effective = self._effective(kind, identifier)
        if effective is None:
            return self._no_file(kind, identifier)
        fields, path, by_default = effective
        words_table = table
        if by_default and kind == "chats":
            words_table = table | verbs.ACCOUNT_ACTIONS
        granted = self._words(fields, path, kind, words_table)
        return self._as_default(
            self._weigh(granted, action, table, kind.rstrip("s"), identifier),
            by_default,
        )

    def _effective(self, kind, identifier):
        """The file that decides, its path, and whether it is the default one.

        A target with no file of its own falls back to `<kind>/default.conf`, so the
        user can grant one action table-wide without a file per target.
        """
        path = self._path(kind, identifier)
        fields = self._read_fields(path)
        if fields is not None:
            return fields, path, False
        path = self.root / kind / f"{DEFAULT}.conf"
        fields = self._read_fields(path)
        if fields is None:
            return None
        return fields, path, True

    def _as_default(self, decision, by_default):
        """Name the default grant in the reason, so a wide grant cannot pass unspoken."""
        if not by_default:
            return decision
        return Decision(
            decision.allowed,
            decision.by,
            f"{decision.reason} by the default grant",
        )

    def _weigh(self, granted, action, table, noun, identifier):
        by = verbs.granted(action, granted, table)
        if by is None:
            held = ", ".join(sorted(granted)) or "nothing"
            return Decision(
                False, None, f"{noun} {identifier} does not allow {action}: holds {held}"
            )
        return Decision(True, by, f"{noun} {identifier} allows {action}")

    def _no_file(self, kind, identifier):
        return Decision(
            False, None, f"no permission file for {kind.rstrip('s')} {identifier}"
        )

    def _granted_words(self, kind, identifier, table):
        """The words the file grants, or None when there is no file."""
        fields = self._fields(kind, identifier)
        if fields is None:
            return None
        return self._words(fields, self._path(kind, identifier), kind, table)

    def _words(self, fields, path, kind, table):
        """The words a parsed file grants, refused when one names no action."""
        words = [w.strip() for w in fields.get("allow", "").split(",") if w.strip()]
        unknown = verbs.unknown_words(words, table)
        if unknown:
            raise PermissionFileError(
                f"{path} grants {', '.join(unknown)}, which is no {kind.rstrip('s')} action"
            )
        return set(words)

    def _fields(self, kind, identifier):
        """Parse the file into fields, or None when there is no file.

        A chat with no file of its own falls back to `chats/default.conf`, so a flag the
        user wrote there holds for every chat as well.
        """
        fields = self._read_fields(self._path(kind, identifier))
        if fields is None and kind == "chats":
            return self._read_fields(self.root / kind / f"{DEFAULT}.conf")
        return fields

    def _read_fields(self, path):
        try:
            text = path.read_text()
        except FileNotFoundError:
            return None
        fields = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition(":")
            if not separator:
                raise PermissionFileError(f"{path}: line is not key: value: {line!r}")
            fields[key.strip()] = value.strip()
        return fields

    def _path(self, kind, identifier):
        return self.root / kind / f"{as_identifier(identifier)}.conf"
