NUMERALS = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
    (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]


def to_roman(n: int) -> str:
    if not 0 < n < 4000:
        raise ValueError("n must be between 1 and 3999")
    out = []
    for value, symbol in NUMERALS:
        while n >= value:
            out.append(symbol)
            n -= value
    return "".join(out)


def from_roman(s: str) -> int:
    text = s.upper()
    total, i = 0, 0
    for value, symbol in NUMERALS:
        while text.startswith(symbol, i):
            total += value
            i += len(symbol)
    if i != len(text) or total == 0 or to_roman(total) != text:
        raise ValueError(f"invalid roman numeral: {s!r}")
    return total
