import re
import unicodedata


def slugify(text: str, sep: str = "-") -> str:
    """Lowercase ASCII slug: 'Hello, World!' -> 'hello-world'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^a-z0-9]", sep, text)
    return text
