---
name: minimal-diff
description: Keeping a change small and reviewable before you finish.
---
Before calling finish, check your diff (`git diff`) against these rules:

- Every changed line is needed for the task or its test. Revert unrelated edits, reformatting and
  renamed variables.
- Match the surrounding style: naming, quotes, import order, error types and docstring format.
- Don't add new dependencies, config files or abstractions the task didn't ask for.
- Keep public function signatures unless the task requires changing them.
- Remove debugging output, commented-out code and stray files (scratch scripts, logs).
- If the change is larger than the task seems to need, say why in your finish summary.
