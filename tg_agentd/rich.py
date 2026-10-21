"""Turn a rich message's blocks back into the Markdown a reader expects.

The server keeps a rich message as Instant View blocks and leaves the text field empty.
Markdown is the form these messages are written in, so it is the form they are read in:
a table stays a table and a formula stays a formula. A block with no text form is named
in brackets rather than dropped, so a reader knows something stood there.
"""

# Inline styles that wrap their text in one marker on each side
WRAPPERS = {
    "TextBold": "**",
    "TextItalic": "*",
    "TextFixed": "`",
    "TextStrike": "~~",
    "TextSpoiler": "||",
}

# Blocks whose name says only what they hold, read as a short label
LABELS = {
    "PageBlockPhoto": "photo",
    "PageBlockVideo": "video",
    "PageBlockAudio": "audio",
    "PageBlockMap": "map",
    "PageBlockEmbed": "embed",
    "PageBlockChannel": "channel",
    "PageBlockCollage": "collage",
    "PageBlockSlideshow": "slideshow",
    "PageBlockUnsupported": "unsupported",
}


def to_markdown(message):
    """The whole message as Markdown, its blocks separated by one blank line."""
    return _blocks(getattr(message, "blocks", None) or [])


def _blocks(blocks):
    return "\n\n".join(rendered for rendered in map(_block, blocks) if rendered)


def _block(block):
    name = type(block).__name__
    if name.startswith("PageBlockHeading") and name[-1].isdigit():
        return "#" * int(name[-1]) + " " + _text(block.text)
    if name == "PageBlockMath":
        return f"$$\n{block.source}\n$$"
    if name == "PageBlockPreformatted":
        return f"```{block.language or ''}\n{_text(block.text)}\n```"
    if name == "PageBlockTable":
        return _table(block)
    if name == "PageBlockList":
        return "\n".join(_item("- ", item) for item in block.items)
    if name == "PageBlockOrderedList":
        return "\n".join(
            _item(f"{item.num or position}. ", item)
            for position, item in enumerate(block.items, start=1)
        )
    if name == "PageBlockDivider":
        return "---"
    if name in ("PageBlockBlockquote", "PageBlockPullquote"):
        return _quoted(_text(block.text))
    if name == "PageBlockBlockquoteBlocks":
        return _quoted(_blocks(block.blocks))
    if name == "PageBlockDetails":
        return (
            f"<details>\n<summary>{_text(block.title)}</summary>\n\n"
            f"{_blocks(block.blocks)}\n</details>"
        )
    if name in LABELS:
        return f"[{LABELS[name]}]"
    # Paragraphs, titles, headers, footers and anything newer than this file: the text
    # they carry is their content, and a block of blocks is read as its blocks
    if getattr(block, "text", None) is not None:
        return _text(block.text)
    if getattr(block, "blocks", None) is not None:
        return _blocks(block.blocks)
    return f"[{name.removeprefix('PageBlock').lower()}]"


def _item(mark, item):
    box = ""
    if getattr(item, "checkbox", None):
        box = "[x] " if getattr(item, "checked", None) else "[ ] "
    if getattr(item, "blocks", None) is not None:
        body = _blocks(item.blocks).replace("\n", "\n" + " " * len(mark))
    else:
        body = _text(item.text)
    return mark + box + body


def _quoted(text):
    return "\n".join("> " + line if line else ">" for line in text.split("\n"))


def _table(block):
    rows = [[_text(cell.text).replace("|", "\\|") for cell in row.cells] for row in block.rows]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    # Markdown names alignment once per column, under the first row, which is the header
    first = block.rows[0].cells
    rule = []
    for column in range(width):
        cell = first[column] if column < len(first) else None
        if getattr(cell, "align_center", None):
            rule.append(":-:")
        elif getattr(cell, "align_right", None):
            rule.append("--:")
        else:
            rule.append("---")
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(rule) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(lines)


def _text(text):
    if text is None:
        return ""
    if isinstance(text, str):
        return text
    name = type(text).__name__
    if name == "TextPlain":
        return text.text
    if name == "TextConcat":
        return "".join(_text(part) for part in text.texts)
    if name in WRAPPERS:
        marker = WRAPPERS[name]
        return marker + _text(text.text) + marker
    if name == "TextUrl":
        return f"[{_text(text.text)}]({text.url})"
    if name == "TextEmail":
        return f"[{_text(text.text)}](mailto:{text.email})"
    if name == "TextMath":
        return f"${text.source}$"
    if name == "TextCustomEmoji":
        return text.alt
    if name == "TextImage":
        return "[image]"
    if name == "TextWithEntities":
        return text.text
    # Underline, marks, mentions, dates and the rest have no Markdown of their own, so
    # they read as the text they wrap
    if getattr(text, "text", None) is not None:
        return _text(text.text)
    return ""
