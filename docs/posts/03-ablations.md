# I turned off each part of my coding agent one at a time. Here's what actually mattered.

_Headline post, week 7. Fill from `results/experiment-full-claude/report.md`; attach `ablation.png` and
`cost_quality.png`. `scripts/update_readme.py` prints the headline numbers._

Everyone building coding agents adds the same things: planning, test-driven loops, reviewers, skills, repo
maps. Almost nobody publishes what each one buys. So I measured it.

**Setup.** Spec-to-PR, my open-source harness, run on [10] fresh tasks with hidden tests. I used five
configurations, each adding one component to the one before:

1. **Bare loop:** sandbox, five tools, budgets
2. **+ Verification:** write a failing test first; the harness proves it fails without the fix and passes
   with it; a reviewer with fresh context checks the diff
3. **+ Skills:** on-demand instruction packs and the repo's `AGENTS.md`
4. **+ Planning:** `plan.md` before any edit
5. **+ Context:** repo map, output trimming, compaction

Three models (Haiku 5.5, Sonnet 5.5, Opus 5.5) × 3 runs each, 450 task runs, $[total] in total.

**What mattered:**

- **[Component]** was worth the most: **[+X] points** of resolve rate on average across models.
- **[Component]** [helped cheap models more than frontier ones / didn't move the needle / cost more than it bought].
- Bare loop to full harness: [Haiku X% → Y%], [Opus X% → Y%].
- Cost per resolved task went [from $A to $B] for [model], because [fewer retries / fewer wasted steps].
- [Surprise: ...]

**What I'd do differently:** [one honest limitation, e.g. 10 tasks is small; spread across runs was ±Z points].

Code, task set and every trajectory are public: [repo link]

#AIEngineering #CodingAgents #Evals #Ablation
