# TODO

## Bugs / Issues

- [x] **Orchestrator marks job as `failed` prematurely** — Root cause: `launcher.ts` used `operation.promise()` which blocks waiting for the Cloud Run job to finish. The gRPC LRO timed out after ~30min, the catch block marked the job as failed. Fix: fire-and-forget the Cloud Run job launch — don't await completion. The worker reports progress via event callbacks; completion/failure detected from terminal events + stale job recovery. Also fixed a bug in `status.ts` where the `completed` event incorrectly marked the job as `failed`.

- [ ] **Task 1 name is wrong** — First task is named `# Build Plan — Todo App` (the markdown heading) instead of an actual task name. Likely a parsing issue in `parse_build_plan()` — the heading line is being treated as a task.

## Performance

- [ ] **Visual Playwright verification is slow** — Each UI task runs a full Playwright verification cycle (start dev server, launch browser, screenshot, evaluate, potentially fix). Task 3 "Custom CSS" took ~12min with 11 screenshots. Consider making per-task visual verification optional and relying on the final E2E sweep instead.

## Completed

- [x] **Commit & push after each phase for resumability** — Planning, scaffolding, each build task, and review now commit+push. Resume detection skips completed phases on re-run.
- [x] **Task-level resume in builder** — On resume, `build_tasks()` checks git log for existing `feat:` commits and skips tasks whose names already appear. So a re-trigger jumps straight to the first incomplete task.
- [x] **`git commit` fails with "Author identity unknown"** — Fixed in Dockerfile with `git config --global` for user.email and user.name.
- [x] **API credits exhausted** — Topped up Anthropic credits.
- [x] **`completed` event marks job as `failed`** — Bug in `status.ts` line 70-71: the `completed` event was in the `failed` branch. Moved to the `completed` branch.
