# Demo: 45 seconds from spec to draft PR

The launch demo shows one real run end to end. Record it on a small public repo you own with a
real, reproducible bug.

## Before recording

1. Docker running, `uv run spec-to-pr build-image` done, `ANTHROPIC_API_KEY` set, `gh auth login` done.
2. A demo repo pushed to GitHub with a real bug and an issue describing it (for example, copy
   `examples/planted_bug` into a new repo and open an issue from `TASK.md`).
3. Do a dry rehearsal with `--dry-run`, so the recording doesn't show the first-run image pull.
4. Terminal at about 110×30, a large font, and a clean prompt.

## Storyboard (about 45 seconds; speed up the waits in editing)

| Time | On screen | Caption |
| --- | --- | --- |
| 0–5s | The GitHub issue | "A real bug, filed as an issue." |
| 5–8s | `spec-to-pr run https://github.com/you/demo/issues/1 --repo .` | "One command." |
| 8–15s | The plan prints; press `a` to approve | "It plans first. I approve." |
| 15–25s | The failing test, then the fix (sped up 4×) | "Test first. Fix in a sandbox with no network." |
| 25–32s | The finish gate passes; the reviewer approves | "It proves the test fails without the fix and passes with it." |
| 32–40s | `Opened draft PR ...`; switch to the PR page | "A draft PR: spec, plan, tests, review, cost." |
| 40–45s | Scroll to the Cost section | "$[X]. Never auto-merged." |

## Recording

The terminal part can be scripted with [VHS](https://github.com/charmbracelet/vhs) using
`docs/demo.tape`, which produces `docs/img/demo.gif`:

```bash
vhs docs/demo.tape
```

Record the browser part (the issue and the PR) separately and join them in any editor. Keep a raw,
uncut version of the run's `runs/<timestamp>/` folder to link from the post, so anyone can check that
the demo wasn't staged.
