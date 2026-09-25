# One-off countdown

An adult can punch a leave-by for today only and start a countdown immediately. It does not change the weekday on-the-road time and it does not change GitHub. The big clock says LEAVE BY that time. Clearing it returns the board to the normal morning rules.

## Sub-features

- `oneoff-start` starts a countdown to the punched time and closes the sheet.
- `oneoff-status` tells the adult the one-off is running, with the clock time.
- `oneoff-keeps-week` leaves `schedule.json` unchanged.
- `oneoff-clear` removes it. The sheet stays open.
- `oneoff-past` refuses a time that is already more than five minutes ago.

## How to get to it (user POV)

- Choose `Adult settings`, tap `One-off leave-by today`, punch the time, choose `Done`, then choose `Start one-off countdown`.
- Choose `Clear one-off` to stop it.

## Driving it with control-morning-board

Preconditions:

- `control-morning-board doctor` prints `ok=true`.
- `storage.morning-board-oneoff=empty`.
- Pick a leave-by that is still later today on the machine clock. `23:30` is later for any run before 11:30 PM local. Do not use a morning hour after that morning has passed; hours 1 through 11 roll forward to PM when AM is already over.

- **Punch 23:30.** Follow the on-page numpad recipe through Done so `#oneOffInput` value is `23:30` and the pad is hidden.
- **Start.** Choose `Start one-off countdown`. Run `control-morning-board browser click --selector '#startOneOff'`. The sheet closes. `body` `data-mode` is `countdown`. `#ampm-indicator` is `LEFT`. `#status-main` contains `LEAVE BY` and `11:30 PM`.
- **Stored record.** Run `control-morning-board browser storage --key morning-board-oneoff`. The JSON `leaveBy` is `23:30` and `date` is today's `YYYY-MM-DD`.
- **Second view.** Open Adult settings. Run `control-morning-board browser click --role button --name "Adult settings"`. `#oneOffStatus` is `One-off running: leave by 11:30 PM today.` and `#oneOffInput` value is `23:30`.
- **Week file unchanged.** Fetch the served file from this run's origin. Run `curl -fsS "$(awk -F= '/^url=/{sub(/\/kiosk.html$/,"/schedule.json",$2); print $2}' <<<"$(control-morning-board doctor)")"`. The JSON Friday `leaveBy` is still `07:40`.
- **Clear.** Choose `Clear one-off`. Run `control-morning-board browser click --selector '#clearOneOff'`. The sheet stays open (`#sheetBg.open` remains). `#oneOffStatus` is `No one-off running.` and `storage.morning-board-oneoff` is `empty`.
- **Proof.** After start, before clear, run `control-morning-board browser screenshot --path "$EVIDENCE/one-off-countdown/running.png" --selector '#status-main'` and `control-morning-board browser snapshot --aria --path "$EVIDENCE/one-off-countdown/running.aria.txt"`. The status line on both artifacts contains `LEAVE BY` and `11:30 PM`. Save the storage JSON in `$EVIDENCE/one-off-countdown/storage.txt`.

## Gotchas

- Hours 1–11 with no AM/PM: the board uses this morning if that time is still ahead, otherwise this afternoon. `7:45` punched at 8 AM becomes `19:45`. `23:30` stays `23:30`. `12:xx` is noon. Midnight is `00:xx`.
- A time already more than five minutes ago is refused. `#oneOffStatus` becomes `That time is already past today. Pick a later leave-by.` and the storage key stays empty.
- Start closes the sheet. Clear does not.
- One-off wins over the weekday countdown and over today's road-time override. Preview hides the one-off until preview is cleared.
- The late label for a one-off is `LEAVE NOW`, not `DOWNSTAIRS NOW`.
- One-off does not subtract the 15-minute downstairs buffer. The countdown runs to the punched clock time itself.
