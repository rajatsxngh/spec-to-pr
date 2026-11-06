def split_line(line: str, delimiter: str = ",") -> list[str]:
    """Split one CSV line. Fields may be wrapped in double quotes; "" inside quotes is a literal quote."""
    fields, current, in_quotes = [], "", False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            if in_quotes and i + 1 < len(line) and line[i + 1] == '"':
                current += '"'
                i += 1
            else:
                in_quotes = not in_quotes
        elif ch == delimiter and not in_quotes:
            fields.append(current)
            current = ""
        else:
            current += ch
        i += 1
    fields.append(current)
    return fields
