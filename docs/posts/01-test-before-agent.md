# I built the test before the agent

_Draft for week 2. Numbers are from the SWE-bench Live pilot (`docs/reports/swebench-pilot/report.md`)._

Week 2 of building Spec-to-PR, a coding agent that turns a written spec into a tested draft pull request.
I haven't made the agent smart yet. I spent the week on the thing that tells me whether it is.

**Why evals first.** SWE-bench Verified is saturated and contaminated; OpenAI stopped reporting it in
February 2026. If I tune an agent against a benchmark it has effectively seen, every improvement is suspect.
So before adding a single feature, I built a harness to measure features.

**What the eval does:**

- 10 self-written tasks (bug fixes and small features), each with **hidden tests** the agent never sees.
  They are copied in only after the agent finishes.
- The hidden set also **restores the original visible tests**, so an agent that "fixes" a bug by gutting a
  test gets graded on the real tests anyway.
- Every task is validated: its hidden tests must fail on the original code and pass with a reference fix.
  The validator caught a bug in one of my own tests on day one.
- A **strict spend cap**: a task starts only if the money spent plus the worst case of every running task
  still fits. A typo in a config can't cost me $500.
- A scoreboard: resolve rate, **cost per resolved task** (my headline metric), time, steps and tokens.

**First numbers.** The bare agent loop resolves 13/20 real bugs from SWE-bench Live at $0.11 per resolved task (Sonnet 5.5, one run each). The minimal baseline,
mini-swe-agent, resolves 11/20 at $0.10.

Next up: making the agent prove its own fixes. More soon.

#AIEngineering #CodingAgents #Evals
