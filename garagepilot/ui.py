"""UI helpers."""

import re

_MARKDOWN_SPECIALS = re.compile(r"([\\`*_\[\]$~<>|#])")


def esc(text: object) -> str:
    """Escape text for Streamlit markdown.

    Streamlit renders `$...$` as LaTeX, so two prices in one line would lose their dollar signs
    and vanish into math. Garage names, customer text and AI output can also contain `*` or `_`.
    """
    return _MARKDOWN_SPECIALS.sub(r"\\\1", str(text))
