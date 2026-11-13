# Spec-to-PR is live

_Launch post, week 8. Attach the 45-second demo (docs/demo.md). Fill [brackets]._

For the last 8 weeks I've been building **Spec-to-PR**, an open-source coding-agent harness: you write a
spec or point it at a GitHub issue, and it opens a tested draft pull request.

[45-second demo]

**What happens in that clip:**

1. The agent reads the repo and drafts a plan: files, steps, tests to add, acceptance criteria. I approve it.
2. It writes a test that reproduces the bug, and the test fails.
3. It fixes the code in a Docker sandbox with no network and no secrets.
4. The harness proves the new test fails without the fix and passes with it. Then a reviewer with fresh
   context checks the diff.
5. A draft PR opens with the spec, plan, test evidence, review verdict and cost: [$X] for this one.

**What I measured:** on a pilot of 20 real bugs from SWE-bench Live, the bare loop solved 65% against 55% for
mini-swe-agent at the same cost; verification, skills and planning added cost and did not solve more.

**What I learned building it:**

- Build the eval before the agent. Every later decision was a number, not a vibe.
- Make the agent prove its work. A finish gate catches "tests pass" claims that aren't true.
- Treat repo text as untrusted. My injection tests plant instructions in READMEs, comments and issues.
- Context engineering is cost engineering.

It never merges anything: every PR is a draft for a human. I've used it for [N] PRs on my own repos and
[N] on projects that welcome AI-assisted contributions (disclosed in each PR).

Repo, task set, results and trajectories: [link]

#AIEngineering #CodingAgents #OpenSource
