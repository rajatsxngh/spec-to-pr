def merge(intervals):
    """Merge overlapping [start, end] intervals; touching intervals ([1, 2], [2, 3]) merge too."""
    result = []
    for start, end in sorted(intervals):
        if result and start < result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result
