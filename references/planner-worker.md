# Optional planner-worker collaboration

Use this mode when the user chooses delegation and the host supports it; ordinary small fixes stay
direct. Keep role/model choices in the conversation or existing host plan, not a new controller or
configuration system. The planner keeps the user's selected model. Use a capable planner for task
decomposition and a different, cheaper worker model for bounded implementation. The planner and
reviewer must be more capable than the worker for their assigned work; the planner can also review.
A different reasoning effort alone does not satisfy the model difference requirement.

Select models and reasoning efforts explicitly using current availability, pricing for the authorized
host/provider and task-relevant capability evidence; model names are not universal defaults. Before
delegating, verify that the worker is available, different and cheaper, and that the planner/reviewer
can provide the stronger judgment required. A different name or higher price alone does not establish
capability. If a requirement is unmet or cannot be established, resolve the model choice before
delegating; never silently substitute a model or introduce an unauthorized service or extra cost.
Optimize total cost, including planning, implementation, review and rework. Keep worker tasks and
context focused; use observed check failures and review findings to revise task scope or model choice
instead of repeatedly spending on an unsuitable worker. Report cost savings only when measured.

The planner defines the outcome, a bounded implementation task, permitted files, acceptance checks
and stopping conditions. Give the worker the relevant current code and evidence, including known
failures, without requiring the entire conversation. The worker implements and runs the relevant
checks, reporting changed files, actual results and unresolved issues. Preserve unrelated work and
coordinate file ownership; use parallel workers only for independent tasks with a concrete benefit.

The more capable reviewer (normally the planner) reviews the actual diff, surrounding integration
points and check evidence against the original requirements before accepting or expanding the work.
Check that the implementation fits the existing code and covers relevant regressions; return concrete
defects to the worker and verify corrections before acceptance. A finished worker turn or passing
technical tests does not establish product completion. Different models can share mistakes; verify
behavior against original evidence. Do not add a mandatory reviewer ensemble, automatic model routing,
new service, or approval gate.
