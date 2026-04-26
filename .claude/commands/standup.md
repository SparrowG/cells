---
description: Daily standup — pick the next clear cells issue, ship it, write wrap-up
---

You are running the morning standup routine for the `SparrowG/cells` repository.

## Hard constraints

- **Only touch `SparrowG/cells`.** Never read from, write to, branch in, or post comments / PRs / issues on any other repo.
- **One task per session.** After you ship one PR, stop and write the wrap-up. Do not start the next task even if there's time or context left.
- **Never force-push, never amend published commits, never push directly to `main`.** Always work on a per-issue branch named `claude/issue-<N>-<slug>`.

## Routine

### 1. Sync state

- `git checkout main && git pull origin main` — confirm working tree is clean. If it isn't, stop and ask before doing anything.
- List open PRs in `SparrowG/cells`. If any are mergeable and look like they're mine (branch name matches `claude/issue-*`), merge them first via squash before starting new work — keep the queue short.
- List open issues in `SparrowG/cells`. Read enough of each to know what's clear and what isn't.

### 2. Pick the next task

Pick the **smallest, clearest, least-blocked** open issue. Tiebreaker order:

1. Direct dependency unlocks for in-flight epic work (e.g. an issue someone else is waiting on).
2. Engine-side / library-side over user-facing — fewer judgment calls.
3. Smaller estimated diff over larger.

If the top candidate has open questions you genuinely can't answer from the issue body, the codebase, and the conversation context: **stop and ask the user.** Do not guess at requirements.

### 3. Ship it

- `git checkout -b claude/issue-<N>-<slug>` off latest `main`.
- Implement to the spec in the issue body. Match the existing code style (don't refactor adjacent code, don't add docstrings the rest of the file doesn't have, no emoji, no speculative abstractions).
- Write tests that cover the contract — not just the happy path. Use `SDL_VIDEODRIVER=dummy` for any test that touches the engine.
- Run `SDL_VIDEODRIVER=dummy python -m pytest -v` and confirm everything passes (not just the new tests).
- Commit with a message that explains the **why**, not the what. Reference the issue with `(#N)` in the subject line.
- `git push -u origin claude/issue-<N>-<slug>`.
- Open a PR via `mcp__github__create_pull_request` with: closes link, summary of approach, test plan checkboxes, and any notes about deferred work.
- Merge the PR via `mcp__github__merge_pull_request` with `merge_method: squash`. (User has standing approval for squash-merging your own PRs — see prior session log.)

### 4. Wrap up

Write a single markdown summary in chat. Format:

```
# Daily standup — <YYYY-MM-DD>

## Done today
| PR | Issue | Title |
|---|---|---|
| #X | #Y  | one-line summary |

Suite: N/N passing in Xs.

## Tomorrow's task — #N <title> (clear / blocked-by-question)
Brief scope. What's already in place. What I'd do.

## Open questions
Numbered list of things that need a decision before the next task.
Skip this section if there are none. Don't invent questions.

## Tasks that could be broken down
Bullet list of follow-up issues I'd file if you say yes.
Each bullet: title + one-sentence rationale + rough size.
Skip this section if there are none.

## Plan ahead
2-4 sentences on multi-week direction, only if state changed enough
to warrant it. Skip otherwise.
```

### 5. Stop

Do not start the next task. Do not "while I'm here..." anything. The routine is done.

## When to deviate

- If `git status` shows uncommitted changes on `main`: stop, surface them, ask.
- If the open-issues list is empty or everything left is blocked on user input: skip step 3, just write a wrap-up that lists the blockers.
- If the test suite is broken on `main` before you start: fix that first as the day's task. A red `main` blocks everything else.
- If a PR you opened in step 1 has CI failures or review comments: fix those first instead of starting a new task. Stale PRs are higher priority than new work.
