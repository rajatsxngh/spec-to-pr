---
name: python-testing
description: Writing a pytest test that reproduces a bug, and running only the tests that matter.
---
Reproduce first, then fix.

1. Find the existing tests for the code you will change (`search` for the function name under `tests/`
   or `test_*.py`). Add your test next to them, in the same style.
2. Write the smallest test that shows the bug: call the function with the input from the task and
   assert the expected result. One behavior per test; name it after the behavior
   (`test_median_even_length`).
3. Run just that test and confirm it fails for the reason you expect:
   `python -m pytest -q path/to/test_file.py::test_name`
   A failure from an import error or typo does not reproduce anything; fix the test first.
4. Make the fix, rerun that test, then run the whole relevant test file or suite.
5. Use `pytest.raises(ValueError)` for expected errors and `pytest.mark.parametrize` for several inputs.
6. Never edit an existing assertion to match buggy output, and never skip or delete a failing test.
