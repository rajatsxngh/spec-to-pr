import math


def paginate(items: list, page: int, per_page: int) -> dict:
    """Return one page (1-based) with metadata."""
    start = page * per_page
    total_pages = math.ceil(len(items) / per_page)
    return {
        "items": items[start:start + per_page],
        "page": page,
        "total_pages": total_pages,
        "has_next": page < total_pages,
    }
