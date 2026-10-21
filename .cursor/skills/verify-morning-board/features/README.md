# Morning board verification map

This directory is the maintained source for verifying the user-facing behavior of morning-board. Read the index before driving the app, then use the matching feature file as the recipe.

## Baseline preconditions

- The site is the static files in this repo. There is no build and no install.
- Launch with `control-morning-board launch` from the repo root. That serves the repo on `127.0.0.1` and opens `index.html`, which redirects to `kiosk.html`, in a throwaway Chrome profile.
- Export `MORNING_BOARD_RUN_ID` from the launch output and pass that same id into doctor, browser, and cleanup.
- The throwaway profile starts with empty `localStorage`. Keys the product writes are `morning-board-today` and `morning-board-oneoff`.
- Run `control-morning-board doctor` and require `ok=true`, this run's URL, and Friday `06:30` / `07:40` in the served `schedule.json`.
- Never drive an instance that was not started by this verification run. Never drive the live GitHub Pages tab.

## Driving conventions

- Start every recipe from the baseline state unless its preconditions say otherwise.
- Prefer ids, `data-key`, and accessible names. The command list is in `../SKILL.md`.
- Treat every command as literal. Keep quoted names and flags unchanged.
- Run browser actions through `control-morning-board browser`.
- The numpad and the time fields listen for pointerdown. Use `browser click`, which sends a real mouse press. Do not set `.value` or call page functions.
- Append command stdout to the evidence directory. Do not remove proof artifacts during cleanup.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the final screen.
- UI proof includes an ARIA snapshot and a screenshot with Morning board visible (page title or the `WEEK FROM GITHUB` heading or the clock status line).
- Mutation proof includes a second user-facing read: `browser storage` for the key, and `GET /schedule.json` to show the weekday file did not change.
- Record the feature id and entry point used with every artifact.
- Report an unreachable path with the attempted command and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with control-morning-board` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

Keep implementation details out of the map. Name only user paths, stable handles, required state, commands, and observable proof.

## Features

- [Household clock](./household-clock.md) covers the idle wall clock, the downstairs countdown, the late state, afternoon preview, and the month calendar.
- [Week from GitHub](./week-from-github.md) covers the adult sheet's read-only weekday alarm and on-the-road times.
- [On-page numpad](./on-page-numpad.md) covers punching a time with no hardware keyboard.
- [Today's road time](./today-road-time.md) covers the calendar-date on-the-road override and clearing it.
- [One-off countdown](./one-off-countdown.md) covers a same-day leave-by that is not the school road time.
