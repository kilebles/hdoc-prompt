"""Helpers for building safe Telegram HTML (parse_mode=HTML) markup.

Telegram's HTML subset only allows a handful of tags (b, i, u, s, a, code,
pre, tg-spoiler, blockquote...). Anything coming from user input MUST be
escaped before being interpolated into a message, otherwise stray `<`/`&`
characters break parsing or let a user inject fake formatting.
"""

from aiogram.utils.text_decorations import html_decoration as hd

escape = hd.quote  # escape(text) -> safe to interpolate into HTML markup
bold = hd.bold
italic = hd.italic
code = hd.code
pre = hd.pre
link = hd.link
spoiler = hd.spoiler


def safe_link(text: str, url: str) -> str:
    """Bold clickable link with an escaped title, safe for arbitrary `text`."""
    return hd.link(hd.quote(text), url)
