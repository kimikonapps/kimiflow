# Optional planner-worker collaboration

Use this mode when the user chooses delegation and the host supports it; ordinary small fixes stay
direct. Keep role/model choices in the conversation or existing host plan, not a new controller or
configuration system. The planner keeps the user's selected model. Use a capable planner for task
decomposition and a worker suited to bounded implementation; the planner can also review. The worker
may run on the same model: its empty context already saves tokens. A different, cheaper worker model
is a recommended option, not a requirement.

In a project initialized with `kimiflow init`, its OPERATIONS.md and [the operating model](operating-model.md)
take precedence: the role table there fixes the models, and reviews run as subagents with a fresh context.

Select models and reasoning efforts explicitly using current availability, pricing for the authorized
host/provider and task-relevant capability evidence. Current model names, strengths and briefing tips
live in the dated [model guide](models.md); it excludes no model. Before delegating,
verify that the chosen worker is available and suited to the task, and that the reviewer can judge
the change. A different name or higher price alone does not establish capability. If a choice is
unsuitable or cannot be established, resolve it before delegating; never silently substitute a model
or introduce an unauthorized service or extra cost.
Optimize total cost, including planning, implementation, review and rework. Keep worker tasks and
context focused; use observed check failures and review findings to revise task scope or model choice
instead of repeatedly spending on an unsuitable worker. Report cost savings only when measured.

The planner defines the outcome, a bounded implementation task, permitted files, acceptance checks
and stopping conditions. Give the worker the relevant current code and evidence, including known
failures, without requiring the entire conversation. The worker implements and runs the relevant
checks, reporting changed files, actual results and unresolved issues. Preserve unrelated work and
coordinate file ownership; use parallel workers only for independent tasks with a concrete benefit.

The reviewer (normally the planner) reviews the actual diff, surrounding integration
points and check evidence against the original requirements before accepting or expanding the work.
Check that the implementation fits the existing code and covers relevant regressions; return concrete
defects to the worker and verify corrections before acceptance. A finished worker turn or passing
technical tests does not establish product completion. Different models can share mistakes; verify
behavior against original evidence. Do not add a mandatory reviewer ensemble, automatic model routing
or new service. Gates apply only where [the operating model](operating-model.md) names them: the
owner's OK on a feature sheet, a plan audit by risk, and the owner-items check (the LIST grep) before a
merge to main.
