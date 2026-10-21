"""A rich message reads as Markdown, with no flag asking for it.

The server keeps a rich message's content as blocks beside an empty text field, so a
reader that takes the text alone sees nothing at all. The service turns the blocks back
into Markdown and puts it where the text would be. A message the server sent only in
part is fetched whole first.
"""

import asyncio

import pytest
from telethon.tl import types as t

from tests.fake_telethon import FakeClient, Message
from tg_agentd import client as client_module
from tg_agentd import handler, permissions, rich


def plain(text):
    return t.TextPlain(text=text)


def cell(text, header=None, align_center=None, align_right=None):
    return t.PageTableCell(
        text=plain(text), header=header, align_center=align_center, align_right=align_right
    )


def rich_message(*blocks, part=None):
    return t.RichMessage(blocks=list(blocks), photos=[], documents=[], part=part)


def lines(blocks):
    return rich.to_markdown(rich_message(*blocks)).splitlines()


def test_a_heading_keeps_its_level():
    assert lines([t.PageBlockHeading2(text=plain("Results"))]) == ["## Results"]


def test_inline_styles_become_their_markdown():
    text = t.TextConcat(
        texts=[
            t.TextBold(text=plain("bold")),
            plain(" "),
            t.TextItalic(text=plain("italic")),
            plain(" "),
            t.TextFixed(text=plain("code")),
            plain(" "),
            t.TextStrike(text=plain("gone")),
            plain(" "),
            t.TextUrl(text=plain("site"), url="https://example.org", webpage_id=0),
            plain(" "),
            t.TextMath(source="x^2"),
        ]
    )
    assert lines([t.PageBlockParagraph(text=text)]) == [
        "**bold** *italic* `code` ~~gone~~ [site](https://example.org) $x^2$"
    ]


def test_a_table_keeps_its_header_and_alignment():
    table = t.PageBlockTable(
        title=t.TextEmpty(),
        rows=[
            t.PageTableRow(
                cells=[cell("model", header=True), cell("WAPE", header=True, align_right=True)]
            ),
            t.PageTableRow(cells=[cell("LGB"), cell("0.166", align_right=True)]),
        ],
    )
    assert lines([table]) == ["| model | WAPE |", "| --- | --: |", "| LGB | 0.166 |"]


def test_a_formula_block_stays_a_formula():
    assert lines([t.PageBlockMath(source="\\sum_i y_i")]) == ["$$", "\\sum_i y_i", "$$"]


def test_lists_keep_their_marks_and_checkboxes():
    bullets = t.PageBlockList(
        items=[
            t.PageListItemText(text=plain("done"), checkbox=True, checked=True),
            t.PageListItemText(text=plain("open"), checkbox=True, checked=False),
            t.PageListItemText(text=plain("plain")),
        ]
    )
    numbered = t.PageBlockOrderedList(
        items=[t.PageListOrderedItemText(text=plain("first"), num="1")]
    )
    assert lines([bullets, numbered]) == [
        "- [x] done",
        "- [ ] open",
        "- plain",
        "",
        "1. first",
    ]


def test_code_keeps_its_language():
    block = t.PageBlockPreformatted(text=plain("print(1)"), language="python")
    assert lines([block]) == ["```python", "print(1)", "```"]


def test_blocks_are_separated_by_a_blank_line():
    assert lines(
        [t.PageBlockParagraph(text=plain("one")), t.PageBlockParagraph(text=plain("two"))]
    ) == ["one", "", "two"]


def test_a_block_with_no_text_form_is_named_rather_than_dropped():
    block = t.PageBlockMap(
        geo=t.GeoPointEmpty(), zoom=1, w=1, h=1, caption=t.PageCaption(
            text=plain(""), credit=plain("")
        )
    )
    assert lines([block]) == ["[map]"]


def test_a_block_this_file_does_not_know_is_still_named():
    assert lines([t.PageBlockAnchor(name="top")]) == ["[anchor]"]


# Through the service


@pytest.fixture
def served(tmp_path, monkeypatch):
    (tmp_path / "chats").mkdir()
    (tmp_path / "folders").mkdir()
    (tmp_path / "chats" / "777.conf").write_text("allow: read\n")

    class _Status:
        def __init__(self, offline):
            self.offline = offline

    monkeypatch.setattr(client_module, "UpdateStatusRequest", _Status)
    whole = rich_message(
        t.PageBlockHeading1(text=plain("Head")), t.PageBlockParagraph(text=plain("tail"))
    )
    fake = FakeClient(
        messages={
            777: [
                Message(id=3, text="", sender_id=42, rich_message=rich_message(
                    t.PageBlockHeading1(text=plain("Head")), part=True
                )),
                Message(id=2, text="", sender_id=42, rich_message=rich_message(
                    t.PageBlockParagraph(text=t.TextBold(text=plain("rich")))
                )),
                Message(id=1, text="**plain**", sender_id=42),
            ]
        },
        full_rich={3: whole},
    )
    service = handler.Handler(permissions.Store(tmp_path), client_module.Client(fake))

    def read():
        answer = asyncio.run(service.handle({"action": "read", "chat": 777}))
        assert answer["ok"] is True, answer
        return {m["id"]: m for m in answer["result"]}, fake

    return read


def test_read_shows_a_rich_message_as_markdown(served):
    messages, _ = served()
    assert messages[2]["text"] == "**rich**"


def test_read_leaves_an_ordinary_message_as_it_was(served):
    messages, _ = served()
    assert messages[1]["text"] == "**plain**"


def test_a_message_sent_in_part_is_fetched_whole(served):
    messages, fake = served()
    assert messages[3]["text"].splitlines() == ["# Head", "", "tail"]
    # Only the partial one costs a request
    assert [call["id"] for call in fake.called("raw_get_rich")] == [3]
