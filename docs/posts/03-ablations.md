# I turned off each part of my coding agent one at a time. Here's what actually mattered.

_Headline post. Numbers are from the pilot (`docs/reports/swebench-pilot/report.md`); attach
`ablation.png` and `cost_quality.png` from there. Rerun on the full grid (`experiments/swebench.toml`) before
making stronger claims._

Everyone building coding agents adds the same things: planning, test-driven loops, reviewers, skills, repo
maps. Almost nobody publishes what each one buys. So I measured it.

**Setup.** Spec-to-PR, my open-source harness, run on 20 real bugs from SWE-bench Live (18 repos), graded by
hidden tests. I used five configurations, each adding one component to the one before, plus mini-swe-agent
as an outside baseline:

1. **Bare loop:** sandbox, five tools, budgets
2. **+ Verification:** write a failing test first; the harness proves it fails without the fix and passes
   with it; a reviewer with fresh context checks the diff
3. **+ Skills:** on-demand instruction packs and the repo's `AGENTS.md`
4. **+ Planning:** `plan.md` before any edit
5. **+ Context:** repo map, output trimming, compaction

This is a pilot: one model (Sonnet 5.5), one run per bug, 114 graded runs, $12.41 in total.

**What I found:**

- **Nothing I added solved more bugs.** Bare loop 65%, + Verification 65%, + Skills 65%, + Planning 60%.
- **Everything I added cost more.** Cost per resolved bug went from $0.11 (bare) to $0.17, $0.18 and $0.23.
- **The bare loop beat mini-swe-agent**, 13 against 11 of 20, at the same cost per resolved bug ($0.11
  against $0.10).
- **My first task set was useless for this.** On 10 tasks I wrote myself, every configuration and every
  model (Haiku, Sonnet, Opus 5.5) solved 100% across 540 runs. Only the real bugs separated anything: 6 of
  the 20 were not solved by any configuration.
- The verification gate is not there only for resolve rate: it attaches proof to the PR that the new test
  fails without the fix. The pilot does not measure what that is worth to a reviewer.

**What I'd do differently:** one run per bug means a 5-point difference is a single bug, so "planning hurt"
is not a finding yet. The + Context cell is incomplete: my API credit ran out and 6 of its 20 runs were lost
(it solved 7 of the 14 it ran; the bare loop solved 8 of the same 14). The full grid, three models and three
runs each, is still to run.

Code, task sets and the result reports are public: [repo link]

#AIEngineering #CodingAgents #Evals #Ablation
