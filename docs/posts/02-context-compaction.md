# How context compaction cut my agent's cost by [X]%

_Draft for week 6. Fill the [bracketed] numbers from the +planning vs +context cells of the experiment._

Coding agents get expensive in a boring way: every step resends the whole conversation, so a 40-step task
pays for step 1's file dump 40 times. This week I added context engineering to Spec-to-PR and measured it.

**Three changes:**

1. **A repo map up front.** A tree-sitter outline of every file, class and function signature goes into the
   first message, so the agent stops spending steps on `ls` and `grep` to find its way around. On my task
   set it averages [N] tokens.
2. **Tighter output trimming.** Long tool output is cut to 8,000 characters, keeping the start and the end.
   That's where the errors and the summary lines usually are.
3. **Compaction near a token budget.** Near 80k tokens, the agent writes a summary of its progress, and a
   fresh conversation starts from the spec, the approved plan, the repo map, the summary and the current diff.

**The gotcha.** My first design kept the recent turns and swapped older ones for a summary. With current
Claude models, editing earlier turns invalidates the model's preserved reasoning, and newer accounts get
the request rejected outright. The shape that stays valid is "simple compaction": summarize, then start
over from a single message. It also happens to be the shape these models are trained on.

**Results** ([N] tasks × 3 runs, [model]):

| | Tokens per task | Cost per resolved task | Resolve rate |
| --- | --- | --- | --- |
| Without context engineering | [A] | [$B] | [C%] |
| With context engineering | [D] | [$E] | [F%] |

That's [X]% cheaper per resolved task, with resolve rate [up/flat/down] [Z] points.

#AIEngineering #ContextEngineering #LLM
