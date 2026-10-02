"""URL slugs from arbitrary text."""
import re
import unicodedata


def slugify(text: str) -> str:
    """Lowercase ASCII words of `text` joined by hyphens: 'Hello, World!' -> 'hello-world'."""
    ascii_text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    words = re.findall(r'[a-z0-9]+', ascii_text.lower())
    return '-'.join(words)
