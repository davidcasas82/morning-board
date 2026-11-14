# Week from GitHub

Adult settings shows the weekday alarm and on-the-road times from `schedule.json`. The tablet cannot edit that week. School mornings here are Monday, Thursday, and Friday. Tuesday, Wednesday, and the weekend are off.

## Sub-features

- `week-open` opens the sheet from the gear.
- `week-loaded` says the week loaded and lists each day's on/off, alarm, and on-the-road time.
- `week-readonly` leaves every on/off control disabled.
- `week-close` closes the sheet from the Close button.

## How to get to it (user POV)

- Choose the gear, whose accessible name is `Adult settings`.
- Choose `Close` to leave the sheet.

## Driving it with control-morning-board

Preconditions:

- `control-morning-board doctor` prints `ok=true` and Friday leave-by `07:40`.
- The page has finished loading the week. `#scheduleStatus` contains `Loaded from GitHub` before the sheet is judged.

- **Wait for the file.** Read the status line. Run `control-morning-board browser wait --selector '#scheduleStatus' --text 'Loaded from GitHub'`. The text is `Loaded from GitHub. School mornings here: Mon, Thu, Fri.`
- **Open the sheet.** Choose `Adult settings`. Run `control-morning-board browser click --role button --name "Adult settings"` and `control-morning-board browser wait --selector '#sheetBg.open'`. A heading `WEEK FROM GITHUB` is present. Run `control-morning-board browser text --selector '#weekRows'` and require `MON`, `6:00 AM`, `7:10 AM`, `TUE`, `OFF`, `WED`, `THU`, `FRI`, `6:30 AM`, `7:40 AM`, `SAT`, and `SUN`.
- **Disabled toggles.** Count enabled on/off controls. Run `control-morning-board browser text --selector '#weekRows'`. The row text shows `ON` for Monday, Thursday, and Friday and `OFF` for the other days. The buttons are disabled; there is no user action that flips them.
- **Close.** Choose `Close`. Run `control-morning-board browser click --selector '#closeSheet'`. `#sheetBg.open` is gone and the clock is visible again.
- **Proof.** With the sheet open, run `control-morning-board browser screenshot --path "$EVIDENCE/week-from-github/sheet.png" --selector '#weekRows'` and `control-morning-board browser snapshot --aria --path "$EVIDENCE/week-from-github/sheet.aria.txt"`. The snapshot includes `WEEK FROM GITHUB`, `FRI`, and `7:40 AM`.

## Gotchas

- Opening the files from `file://` cannot fetch `schedule.json`. The status then says it could not load. Drive the HTTP origin from launch.
- The status text updates while the sheet is closed. Wait for `Loaded from GitHub` before judging `#weekRows`.
- Clicking the dim area behind the sheet is an unreliable close: the sheet covers most of the viewport and swallows the click. Use `#closeSheet`.
- This sheet does not save a private week. A proof that types into a weekday row is not this feature; the rows are not editable.
