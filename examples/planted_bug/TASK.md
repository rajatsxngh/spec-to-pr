`median()` in `stats.py` returns the wrong value (or crashes) for lists with an even number of items.
For an even-length list it should return the average of the two middle values, e.g. `median([4, 1, 3, 2]) == 2.5`.

Fix the bug and make sure `pytest` passes.
