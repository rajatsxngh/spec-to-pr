def split_line(line: str, delimiter: str = ",") -> list[str]:
    """Split one CSV line. Fields may be wrapped in double quotes; "" inside quotes is a literal quote."""
    fields, current, in_quotes = [], "", False
    for ch in line:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == delimiter and not in_quotes:
            fields.append(current)
            current = ""
        else:
            current += ch
    fields.append(current)
    return fields
