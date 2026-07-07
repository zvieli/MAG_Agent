import arabic_reshaper
from bidi.algorithm import get_display


def fix_text(text: str) -> str:
    """Prepares Hebrew/Arabic text for proper RTL rendering in CLI environments.

    Args:
        text: The raw string containing RTL characters.

    Returns:
        The reshaped and reordered string safe for terminal display.
    """
    if not isinstance(text, str):
        text = str(text)
    reshaped_text = arabic_reshaper.reshape(text)
    bidi_text = get_display(reshaped_text)
    return bidi_text
