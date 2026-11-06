# Household clock

The leftover tab shows one shared clock. Off days and times outside the morning window show the wall clock. A school morning counts down to downstairs (15 minutes before on-the-road). At downstairs it shows DOWNSTAIRS NOW and how late. Adults can preview those states in the afternoon without moving the computer clock.

## Sub-features

- `clock-idle` shows the wall clock, an AM/PM mark, and the weekday date when no countdown is running.
- `clock-countdown` shows remaining minutes and seconds, the word LEFT, and DOWNSTAIRS BY the downstairs time.
- `clock-late` shows DOWNSTAIRS NOW and a `+M:SS LATE` status.
- `clock-preview` jumps the display to five minutes left, to zero, and back to the live clock.
- `calendar-today` marks today's date on the month grid.

## How to get to it (user POV)

- Open the site root. `index.html` sends the browser to `kiosk.html`.
- Open `kiosk.html` directly.
- Open Adult settings and choose `Jump to 5 min left`, `Hit zero`, or `Back to live clock`.

## Driving it with control-morning-board

Preconditions:

- `control-morning-board doctor` prints `ok=true`.
- `storage.morning-board-today=empty` and `storage.morning-board-oneoff=empty`.
- Note `browser_timezone` from the doctor. Countdown windows match the household only when it is `America/Los_Angeles`.

- **Open the board.** Open the root the way the tablet does. Run `control-morning-board browser open --path /`. The printed `opened=` URL contains `kiosk.html`.
- **Idle clock.** Read the mode when the local time is outside a school morning (before alarm, after downstairs plus five minutes, or an off day). Run `control-morning-board browser attr --selector body --name data-mode`, `control-morning-board browser attr --selector body --name data-band`, `control-morning-board browser text --selector '#ampm-indicator'`, and `control-morning-board browser text --selector '#status-main'`. `data-mode` is `clock`, `data-band` is `plenty`, the indicator is `AM` or `PM`, and the status is the weekday date such as `FRI 25 SEP`.
- **Calendar.** Read the month header and the marked day. Run `control-morning-board browser text --selector '#cal-header'` and `control-morning-board browser text --selector '.cal-day.today'`. The header is the local month and year (`SEP 2026`) and the marked day is today's date number.
- **Five minutes left.** Choose Adult settings, then `Jump to 5 min left`. Run `control-morning-board browser click --role button --name "Adult settings"`, `control-morning-board browser wait --selector '#sheetBg.open'`, and `control-morning-board browser click --selector '#jumpFive'`. The sheet closes (`#sheetBg.open` is gone), `data-mode` is `countdown`, `data-band` is `red`, `#ampm-indicator` is `LEFT`, and `#status-main` contains `DOWNSTAIRS BY`.
- **Hit zero.** Open Adult settings and choose `Hit zero`. Run `control-morning-board browser click --role button --name "Adult settings"` and `control-morning-board browser click --selector '#jumpZero'`. `data-mode` is `late`, `#leave-now` is `DOWNSTAIRS NOW`, and `#status-main` contains `LATE`.
- **Back to live.** Open Adult settings and choose `Back to live clock`. Run `control-morning-board browser click --role button --name "Adult settings"` and `control-morning-board browser click --selector '#clearPreview'`. `data-mode` returns to `clock` when the real local time is outside the morning window.
- **Storage untouched.** Read both keys. Run `control-morning-board browser storage --key morning-board-today` and `control-morning-board browser storage --key morning-board-oneoff`. Both are `empty`.
- **Proof.** Capture the five-minute countdown and the late screen. Run `control-morning-board browser screenshot --path "$EVIDENCE/household-clock/five.png" --selector '#status-main'` and `control-morning-board browser snapshot --aria --path "$EVIDENCE/household-clock/five.aria.txt"` while `data-mode` is `countdown`, then the same pair as `late.png` and `late.aria.txt` while `#leave-now` reads `DOWNSTAIRS NOW`. The snapshots name Morning board and include the status text.

## Gotchas

- SVG digit cells (`#d0` through `#d3`) have no text to read. Assert `#status-main`, `#ampm-indicator`, `#leave-now`, and `data-mode`.
- The countdown ticks four times a second. Assert `LEFT` and `DOWNSTAIRS BY`, not a frozen `05:00`.
- Preview does not write `localStorage` and does not change `schedule.json`. It does change the on-screen mode until `Back to live clock`.
- An active one-off is hidden while preview is running and comes back when preview clears. Start this recipe with both storage keys empty.
- `#jumpFive` and `#jumpZero` close the sheet. `#clearPreview` closes it too.
- If `browser_timezone` is not `America/Los_Angeles`, the idle-versus-countdown boundary is not the household's morning. Say so; do not treat a countdown at the wrong hour as a schedule bug.
- Off days (Tuesday, Wednesday, Saturday, Sunday in `schedule.json`) stay on the wall clock all day unless a one-off is running.
