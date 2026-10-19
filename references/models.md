# Claude models for Kimiflow roles

Claude Code / Anthropic reference. As of 2026-10-19. Current availability and pricing decide.

This file is a recommendation, not a rule. Every model may take every role; the planner chooses and
names the choice. Statements marked **[S]** cite the linked Anthropic documentation. Statements marked
**[E]** are experience, 2026-10, and have no official source.

Sources:
[models] https://platform.claude.com/docs/en/about-claude/models/overview ·
[effort] https://platform.claude.com/docs/en/build-with-claude/effort ·
[best] https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices ·
[fable] https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5 ·
[fable51] https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 ·
[opus] https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5 ·
[sonnet] https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 ·
[haiku] https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5 ·
[haiku-new] https://platform.claude.com/docs/en/models/haiku-5-5/whats-new-haiku-5-5 ·
[subagents] https://code.claude.com/docs/en/sub-agents ·
[config] https://code.claude.com/docs/en/model-config ·
[fast] https://code.claude.com/docs/en/fast-mode

## Current models

| Model | API ID / Claude Code alias | Price in/out per MTok | API default effort | Positioning [models] |
| --- | --- | --- | --- | --- |
| Claude Fable 5.1 | `claude-fable-5-1` / `fable` | $10 / $50 | `high` | Demanding reasoning, long-horizon agentic work |
| Claude Opus 5.5 | `claude-opus-5-5` / `opus` | $4 / $20 | `medium` | Long-running agentic coding and knowledge work |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` / `sonnet` | $2 / $10 | `high` | Best combination of speed and intelligence |
| Claude Haiku 5.5 | `claude-haiku-5-5` / `haiku` | from $0.10 / $0.50 | `medium` | High-volume, latency-sensitive tasks |

All four have a 1M-token context window and 128K max output [S, models]. In Claude Code, Opus 5.5,
Sonnet 5.5 and Haiku 5.5 default to `medium` effort; other models mostly to `high`, and an
organization can set its own default [S, config].
Levels are `low`, `medium`, `high`, `xhigh`, `max` [S, effort]. Level names do not mean the same amount
of thinking across models, so compare by result, not by label [S, opus; sonnet].

## Per model

**Claude Fable 5.1.** Documented strengths: long-horizon autonomy, first-shot correctness on complex,
well-specified problems, code review and debugging, navigating ambiguity, and dispatching and
sustaining parallel subagents [S, fable]. At `low` it is often competitive with Opus and Sonnet on
cost per task [S, fable51]. Typical roles: planner, architect, reviewer of hard changes; also a strong
worker on the hardest slice. Effort: start at `high`; `medium`/`low` for routine work [S, effort].
Briefing: state goal and constraints rather than steps, because skills written for earlier models are
often too prescriptive and can degrade output [S, fable]. Give the reason behind the request [S, fable].
Name what to leave out; it can otherwise extend scope [S, fable51].

**Claude Opus 5.5.** Documented strengths: multistep agentic coding in a real repository, code review
with fewer false alarms, long autonomous runs with parallel subagents, knowledge work with fewer wrong
figures, and plain-language reports [S, opus]. At `medium` it matched or beat Opus 5 at `high` on
such tasks [S, opus]. Typical roles: planner, worker, reviewer. Effort: start at `medium`, set it
explicitly, and lower effort before prompting for less thinking [S, opus]. Briefing: for unattended
runs, keep the task's parts in a checklist so a progress report is not mistaken for completion [S, opus].

**Claude Sonnet 5.5.** Positioned as the best combination of speed and intelligence [S, models]; for the
hardest long-horizon work an Opus model is the better choice [S, sonnet]. Typical roles: worker for well-specified implementation, reviewer of bounded diffs,
planner for medium tasks. Effort: `medium` for well-specified agentic coding, `high` for harder or
longer tasks [S, effort]. Briefing: at `low`/`medium` it may check in before finishing, and at `low` it
sometimes reports a change as done without running a check; ask it to keep going until done and to
run a check that exercises the change [S, sonnet].

**Claude Haiku 5.5.** Documented purpose: high-volume, latency-sensitive work such as classification,
routing, extraction and subagent tasks [S, haiku-new].
Typical roles: mechanical tasks such as log scans, list or link checks, inventory, and bounded
single-file changes; may also work as an implementation worker with a tight brief. Effort: `medium`
for most work including agentic coding, `low` for short tool tasks, `high` for longer agent tasks and
strict instruction following [S, haiku].

### Getting good results from smaller or faster models

- One coherent package per brief: a few files, one outcome [E].
- Exact commands to run, with expected output, rather than "run the tests" [E].
- Compact context: paste the relevant code excerpt instead of pointing at a large tree [E].
- Explicit acceptance checks and a done condition. In long agent prompts at `low` effort Haiku may
  stop early; a keep-working instruction or `medium` effort helps [S, haiku].
- Ask for a real check before "done"; at `low` Sonnet sometimes skips it [S, sonnet].

## Roles and model choice

- The planner may choose the same model for the worker. A worker subagent starts with an empty context,
  which already saves tokens over a long planner session [E]. A cheaper worker model is an option, not
  a requirement.
- In Claude Code, pass the choice explicitly via the subagent `model` parameter (`fable`, `opus`,
  `sonnet`, `haiku`, a full ID, or `inherit`) [S, subagents]. Resolution order: per-invocation `model`,
  then the agent's `model` frontmatter, then `CLAUDE_CODE_SUBAGENT_MODEL`, then the main conversation's
  model [S, subagents]. Without an explicit choice the subagent can end up on the session model.
- Name the chosen model per role in the project's role table, so the choice is visible and reviewable.
- Fresh-context verifier subagents tend to outperform self-critique for long runs [S, fable]. A reviewer
  on a different model is optional.

## Writing rules, briefs and agents for Claude 5 models

- **Give the reason.** Context and motivation behind an instruction help Claude target the result and
  generalize from it [S, best]; Fable does better when it knows the intent [S, fable].
- **Calm, plain wording.** Newer models are more responsive to the system prompt; replace "CRITICAL:
  You MUST" with "Use this tool when…", since aggressive language causes overtriggering [S, best].
- **Full relevant context in the brief.** Treat the reader as a capable new colleague without your
  context; if a colleague would be confused, so will Claude [S, best].
- **Room for judgement.** Prefer goal and constraints over step lists for capable models [S, fable].
  Use numbered steps where order or completeness matters [S, best].
- **Parallel tool calls.** Ask for independent calls in one response; sequential only when one depends
  on another [S, best].
- **Self-verification before reporting.** Audit each progress claim against a tool result from the
  session [S, fable]; run a real check that exercises the change [S, sonnet; haiku].
- **No over-prompting.** Instructions like "If in doubt, use [tool]" cause overtriggering; remove
  anti-laziness prompting written for older models [S, best]. Lower effort reduces thinking more
  reliably than prompt instructions [S, opus].
- **Scope.** Say what to leave out; a short instruction is enough [S, fable; fable51].

## Host features worth using (Claude Code)

- **Subagents with an explicit `model`** and optional `effort` frontmatter (`low`…`max`, model
  dependent) [S, subagents].
- **Background subagents.** Claude runs a subagent in the background by default and in the foreground
  when it needs the result first; `background: true` keeps it in the background [S, subagents]. Fable
  works well when the lead keeps working while subagents run [S, fable51].
- **Worktrees.** `isolation: worktree` runs a subagent in a temporary git worktree, removed if it made
  no changes [S, subagents].
- **Effort.** `/effort`, `--effort`, `CLAUDE_CODE_EFFORT_LEVEL` or the `effortLevel` setting [S, config].
- **Fast mode** (research preview). Same Opus model up to 2.5x faster at higher per-token cost
  ($8/$40 on Opus 5.5); Opus only; toggle with `/fast` [S, fast]. Useful for interactive iteration,
  not for long autonomous runs [S, fast].
