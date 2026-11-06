def wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap. Words longer than width are split across lines."""
    lines, current = [], ""
    for word in text.split():
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
