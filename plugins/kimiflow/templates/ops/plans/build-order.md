# Build order — <batch>

Written after a batch of LIST items, before any worker starts. The planner decides grouping and order;
the owner sees the result, nobody is asked. Rewrite it per batch; the old version goes to `archive/`.

## Central parts first

- <shared component or contract> — built and committed before the packages that use it

## Packages

| Package | Items | Files owned | Size | Flow | Lane |
| --- | --- | --- | --- | --- | --- |
| <name> | <LIST IDs> | <paths no other package touches> | S/M/L | full / short / bug | <1> |

Packages in different lanes own disjoint files and run in parallel. An L package gets two workers
from the start (core, then tests and leftovers).

## Order

1. <package> → verify: <gate>
