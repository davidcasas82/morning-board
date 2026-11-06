# Today's road time

An adult can set today's on-the-road time without changing the weekday file. The override lasts for this calendar date only. Clear puts today back on the weekday time. Downstairs stays 15 minutes before whatever on-the-road time is in effect.

## Sub-features

- `today-apply` stores a normalized road time for today's date and closes the sheet.
- `today-keeps-week` leaves `schedule.json` unchanged.
- `today-clear` removes the override and shows the weekday time in the field again.

## How to get to it (user POV)

- Choose `Adult settings`, tap `Today’s on-the-road time`, punch a time on the pad, choose `Done`, then choose `Use this road time today`.
- Choose `Clear today’s override` to drop it.

## Driving it with control-morning-board

Preconditions:

- `control-morning-board doctor` prints `ok=true`.
- `storage.morning-board-today=empty` and `storage.morning-board-oneoff=empty`.
- The numpad recipe's replace step did not leave `#leaveInput` as a partial digit. If it did, clear the pad to empty and tap Done before this recipe.

- **Punch 08:10.** Open Adult settings, tap `#leaveInput`, tap `0`, `8`, `1`, `0`, then Done. Run `control-morning-board browser click --role button --name "Adult settings"`, `control-morning-board browser click --selector '#leaveInput'`, then `control-morning-board browser click --selector 'button[data-key="0"]'` and the same for `8`, `1`, `0`, and `done`. `#leaveInput` value is `08:10`.
- **Apply.** Choose `Use this road time today`. Run `control-morning-board browser click --selector '#applyLeave'`. The sheet closes. `storage.morning-board-today` is JSON whose `leaveBy` is `08:10` and whose `date` is today's `YYYY-MM-DD` from the machine clock.
- **Week file unchanged.** Fetch the served file from this run's origin. Run `curl -fsS "$(awk -F= '/^url=/{sub(/\/kiosk.html$/,"/schedule.json",$2); print $2}' <<<"$(control-morning-board doctor)")"`. The JSON Friday `leaveBy` is still `07:40`.
- **Second view.** Open Adult settings again. Run `control-morning-board browser click --role button --name "Adult settings"`. `#leaveInput` value is `08:10` and `#scheduleStatus` still contains `Loaded from GitHub`.
- **Clear.** Choose the clear button by id (the accessible name contains a curly apostrophe). Run `control-morning-board browser click --selector '#resetLeave'`. `storage.morning-board-today` is `empty`. `#leaveInput` shows the weekday road time when today is Monday, Thursday, or Friday (`07:10` or `07:40`), and is empty on an off day.
- **Proof.** After apply, before clear, run `control-morning-board browser screenshot --path "$EVIDENCE/today-road-time/applied.png" --selector '#status-main'` and `control-morning-board browser snapshot --aria --path "$EVIDENCE/today-road-time/applied.aria.txt"`. Save the storage line in `$EVIDENCE/today-road-time/storage.txt`. The shot is the board after the sheet closed; the storage file is the second view of the saved date and `08:10`.

## Gotchas

- The clear button's accessible name is `Clear today’s override` with the Unicode apostrophe `’` (U+2019). Prefer `#resetLeave`.
- Apply uses the computer's calendar date, not a previewed morning. Preview buttons do not change which date gets stored.
- Apply does nothing when the field is empty or not a real time. The sheet stays open and the storage key stays empty.
- A running one-off hides this override on the big clock until the one-off is cleared. Keep `morning-board-oneoff` empty when proving the override on the clock.
- This does not change GitHub and does not change Tuesday into a school day. Off days stay on the wall clock unless a one-off is started.
