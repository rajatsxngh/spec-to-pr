---
name: debugging
description: Narrowing down why code misbehaves when the cause isn't obvious from reading it.
---
1. Reproduce the failure with one command and keep that command; rerun it after every change.
2. Read the full traceback from the bottom up. The last frame in the repository's own code is
   usually where to start, not the library frame below it.
3. Form one hypothesis at a time and test it with the cheapest check: a quick
   `python -c "..."` call, a print, or a focused test. Remove temporary prints before finishing.
4. Check boundaries first: empty input, one element, even versus odd lengths, exact limits
   (`<` versus `<=`), off-by-one indexes, None, and unicode.
5. If the code is used in several places, `search` for the callers before changing behavior, so the
   fix doesn't break them.
6. When stuck after a few attempts, step back and reread the task: the bug may be elsewhere.
