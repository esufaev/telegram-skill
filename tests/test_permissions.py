"""Permissions are read from a directory the agent cannot write, and refuse by default."""

import pytest

from tg_agentd import permissions, verbs


@pytest.fixture
def store(tmp_path):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    return permissions.Store(tmp_path)


def write(store, kind, name, text):
    path = store.root / kind / f"{name}.conf"
    path.write_text(text)
    return path


def test_a_chat_with_no_file_grants_nothing(store):
    decision = store.chat_may(-1001234567890, "read")
    assert not decision.allowed
    assert "no permission file" in decision.reason


def test_a_granted_action_names_the_word_that_granted_it(store):
    write(store, "chats", "-1001234567890", "allow: read, send\n")
    decision = store.chat_may(-1001234567890, "send")
    assert decision.allowed
    assert decision.by == "send"


def test_a_wildcard_grant_says_that_it_was_wide(store):
    write(store, "chats", "777", "allow: all\n")
    decision = store.chat_may(777, "delete-for-all")
    assert decision.allowed
    assert decision.by == verbs.WILDCARD


def test_an_action_outside_the_grant_is_refused_with_what_is_held(store):
    write(store, "chats", "777", "allow: read\n")
    decision = store.chat_may(777, "media")
    assert not decision.allowed
    assert "read" in decision.reason


def test_an_undefined_word_in_the_file_is_an_error_not_a_silent_pass(store):
    write(store, "chats", "777", "allow: read, shout\n")
    with pytest.raises(permissions.PermissionFileError) as caught:
        store.chat_may(777, "read")
    assert "shout" in str(caught.value)


def test_a_flag_is_read_as_a_boolean(store):
    write(store, "chats", "777", "allow: reply\nmark-read-on-reply: yes\n")
    assert store.chat_flag(777, "mark-read-on-reply") is True
    write(store, "chats", "778", "allow: reply\n")
    assert store.chat_flag(778, "mark-read-on-reply") is False


def test_blank_lines_and_comments_are_ignored(store):
    write(store, "chats", "777", "# notes for the reader\n\nallow: read\n\n")
    assert store.chat_may(777, "read").allowed


# The invariant the whole folder design rests on: a folder that lets the agent read what
# is inside it must not be a folder the agent can put more chats into
def test_a_folder_granting_content_refuses_to_be_edited(store):
    write(store, "folders", "5", "allow: read, folder-edit\n")
    decision = store.folder_may(5, "folder-edit")
    assert not decision.allowed
    assert "grants content" in decision.reason


def test_a_folder_granting_no_content_may_be_edited(store):
    write(store, "folders", "6", "allow: folder-read, folder-edit\n")
    assert store.folder_may(6, "folder-edit").allowed


def test_a_wildcard_folder_is_not_editable_either(store):
    write(store, "folders", "7", "allow: all\n")
    assert store.folder_may(7, "read").allowed
    assert not store.folder_may(7, "folder-edit").allowed


def test_a_folder_never_grants_a_write_action_on_its_chats(store):
    write(store, "folders", "8", "allow: all\n")
    with pytest.raises(permissions.UnknownAction):
        store.folder_may(8, "send")


# The identifier arrives from the agent, and a path is built from it
@pytest.mark.parametrize(
    "identifier",
    ["../../etc/passwd", "-100/../../x", "x/y", "", ".", "..", "7\n8", "/abs"],
)
def test_an_identifier_that_is_not_a_plain_number_is_refused(store, identifier):
    with pytest.raises(permissions.BadIdentifier):
        store.chat_may(identifier, "read")


def test_a_negative_identifier_is_ordinary(store):
    # Groups are negative and channels carry a -100 prefix, so the sign is data
    write(store, "chats", "-1001234567890", "allow: read\n")
    assert store.chat_may("-1001234567890", "read").allowed


def test_a_default_file_grants_every_chat_without_its_own(store):
    write(store, "chats", permissions.DEFAULT, "allow: read, media\n")
    assert store.chat_may(777, "read").allowed
    assert store.chat_may(-1001234567890, "media").allowed


def test_a_chats_own_file_replaces_the_default(store):
    write(store, "chats", permissions.DEFAULT, "allow: read, media\n")
    write(store, "chats", "777", "allow: read\n")
    assert not store.chat_may(777, "media").allowed
    assert store.chat_may(778, "media").allowed


def test_the_answer_names_the_default_when_it_decided(store):
    write(store, "chats", permissions.DEFAULT, "allow: read\n")
    assert "default" in store.chat_may(777, "read").reason
    assert "default" in store.chat_may(777, "media").reason


def test_a_flag_falls_back_to_the_default(store):
    write(store, "chats", permissions.DEFAULT, "allow: reply\nmark-read-on-reply: yes\n")
    assert store.chat_flag(777, "mark-read-on-reply") is True


def test_the_survey_lists_the_default_grant(store):
    write(store, "chats", permissions.DEFAULT, "allow: read, media\n")
    assert store.survey()["chats"]["default"] == ["media", "read"]


def test_an_undefined_word_in_the_default_is_an_error(store):
    write(store, "chats", permissions.DEFAULT, "allow: shout\n")
    with pytest.raises(permissions.PermissionFileError):
        store.chat_may(777, "read")


def test_a_default_folder_file_grants_every_folder_without_its_own(store):
    write(store, "folders", permissions.DEFAULT, "allow: digest\n")
    assert store.folder_may(5, "digest").allowed
    assert "default" in store.folder_may(5, "digest").reason


def test_a_folders_own_file_replaces_the_default(store):
    write(store, "folders", permissions.DEFAULT, "allow: digest\n")
    write(store, "folders", "5", "allow: folder-read\n")
    assert not store.folder_may(5, "digest").allowed
    assert store.folder_may(6, "digest").allowed


def test_a_default_folder_granting_content_still_refuses_folder_edit(store):
    write(store, "folders", permissions.DEFAULT, "allow: digest, folder-edit\n")
    assert not store.folder_may(5, "folder-edit").allowed


def test_a_folder_field_does_not_come_from_the_default(store):
    write(store, "folders", permissions.DEFAULT, "allow: digest\nmembers: recorded\n")
    assert store.folder_field(5, "members") == ""


def test_the_survey_lists_the_default_folder_grant(store):
    write(store, "folders", permissions.DEFAULT, "allow: digest\n")
    assert store.survey()["folders"]["default"] == ["digest"]


def test_the_default_grant_may_list_every_chat(store):
    write(store, "chats", permissions.DEFAULT, "allow: read, dialogs\n")
    decision = store.account_may("dialogs")
    assert decision.allowed
    assert decision.by == "dialogs"


def test_the_wildcard_does_not_grant_the_account_list(store):
    write(store, "chats", permissions.DEFAULT, "allow: all\n")
    assert not store.account_may("dialogs").allowed


def test_a_per_chat_file_may_not_grant_the_account_list(store):
    write(store, "chats", "777", "allow: dialogs\n")
    with pytest.raises(permissions.PermissionFileError):
        store.chat_may(777, "read")


def test_the_survey_lists_the_account_action_under_the_default(store):
    write(store, "chats", permissions.DEFAULT, "allow: dialogs\n")
    assert store.survey()["chats"]["default"] == ["dialogs"]
