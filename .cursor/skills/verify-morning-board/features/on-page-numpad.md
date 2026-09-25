# On-page numpad

The Surface has no keyboard. Tapping a time field opens a number pad on the sheet. Digits, backspace, and Done update that field. The operating-system keyboard stays closed because the field is readonly and `inputmode` is `none`.

## Sub-features

- `numpad-open` opens the pad from either time field and does not leave focus in the field.
- `numpad-type` builds the time in the field and in `#timePadPreview`.
- `numpad-backspace` removes the last digit.
- `numpad-done` writes a normalized `HH:MM` and hides the pad.
- `numpad-replace` replaces an existing value on the first new digit.

## How to get to it (user POV)

- Choose `Adult settings`, then tap `Today’s on-the-road time` (`#leaveInput`).
- Choose `Adult settings`, then tap `One-off leave-by today` (`#oneOffInput`).

## Driving it with control-morning-board

Preconditions:

- `control-morning-board doctor` prints `ok=true`.
- The adult sheet is closed and `#timePad` still has class `hidden`.
- `$EVIDENCE` is the `evidence_dir` launch printed.

- **Open from the one-off field.** Choose `Adult settings`, then tap the one-off field. Run `control-morning-board browser click --role button --name "Adult settings"`, `control-morning-board browser wait --selector '#sheetBg.open'`, and `control-morning-board browser click --selector '#oneOffInput'`. `#timePad` loses class `hidden`, `#timePadLabel` is `One-off leave-by`, and `#timePadPreview` is `--:--` when the field was empty.
- **No OS keyboard.** Read the field and the focused element. Run `control-morning-board browser attr --selector '#oneOffInput' --name readonly`, `control-morning-board browser attr --selector '#oneOffInput' --name inputmode`, and `control-morning-board browser active`. `readonly` is present, `inputmode` is `none`, and `active_id` is not `oneOffInput`.
- **Punch 23:30.** Tap `2`, `3`, `3`, then read the field before the last digit. Run `control-morning-board browser click --selector 'button[data-key="2"]'`, then the same command for `3` and `3`. `#timePadPreview` and `#oneOffInput` are `2:33`. Tap `0`. Run `control-morning-board browser click --selector 'button[data-key="0"]'`. Both are `23:30`.
- **Proof of the open pad.** While the pad is still open on `23:30`, run `control-morning-board browser screenshot --path "$EVIDENCE/on-page-numpad/entering.png" --selector '#timePad'` and `control-morning-board browser snapshot --aria --path "$EVIDENCE/on-page-numpad/entering.aria.txt"`. The snapshot includes button `2`, `RootWebArea` name `Morning board`, and textbox `ONE-OFF LEAVE-BY TODAY` with value `23:30`.
- **Backspace.** Tap Backspace. Run `control-morning-board browser click --role button --name "Backspace"`. The preview and the field are `23:3`. Wide form stays until two or fewer digits remain, so this is not `2:33`.
- **Restore the last digit.** Tap `0` again. Run `control-morning-board browser click --selector 'button[data-key="0"]'`. The preview and the field are `23:30`.
- **Done.** Tap `Done`. Run `control-morning-board browser click --selector 'button[data-key="done"]'`. `#timePad` has class `hidden` and `#oneOffInput` value is `23:30`. `localStorage` `morning-board-oneoff` stays `empty` until a later feature starts the countdown.
- **Proof after Done.** Run `control-morning-board browser screenshot --path "$EVIDENCE/on-page-numpad/done.png" --selector '#oneOffInput'` and `control-morning-board browser snapshot --aria --path "$EVIDENCE/on-page-numpad/done.aria.txt"`. The snapshot still shows value `23:30` on `ONE-OFF LEAVE-BY TODAY`. Write `feature=on-page-numpad` and `entry=#oneOffInput` into `$EVIDENCE/on-page-numpad/entry.txt`.
- **Replace on the road field.** Tap `#leaveInput` and then `9`. Run `control-morning-board browser click --selector '#leaveInput'` and `control-morning-board browser click --selector 'button[data-key="9"]'`. `#timePadLabel` is `Today road time`. The field becomes `9`, not the old time with a 9 appended. Tap Backspace until the preview is `--:--`, then tap `Done`, so a later override recipe is not stuck with `9`. Run `control-morning-board browser click --role button --name "Backspace"` and `control-morning-board browser click --selector 'button[data-key="done"]'`.

## Gotchas

- Hardware key presses do not fill these fields. The product path is the pad.
- `button[data-key="back"]` is the backspace key. Its accessible name is `Backspace`, not the glyph.
- The first digit after opening a field that already has a time replaces that time. Capture the cleared-or-replaced value before asserting a full `HH:MM`.
- Done normalizes `7:45` to `07:45` when the time is valid. An impossible time such as `99:99` is left as typed and the pad still closes.
- Three digits render as `H:MM` (`233` displays `2:33`) only before a fourth digit has been entered. After four digits the pad stays wide: backspace from `23:30` displays `23:3`, then `23`, then `2`.
- Punching a time does not write `morning-board-today` or `morning-board-oneoff` and does not start a countdown. Starting it is the one-off feature.
- The pad is `pointerdown` only. A DOM `.click()` that never presses the pointer does not type a digit.
