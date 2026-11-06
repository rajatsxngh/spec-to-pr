def wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap. Words longer than width are split across lines."""
    if width < 1:
        raise ValueError("width must be at least 1")
    lines, current = [], ""
    for word in text.split():
        if len(word) > width:
            if current:
                lines.append(current)
                current = ""
            while len(word) > width:
                lines.append(word[:width])
                word = word[width:]
            if not word:
                continue
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
