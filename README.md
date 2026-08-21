# morning-board

Kids morning leave-by countdown for an old Surface Pro.

**v1 is one shared clock** — the household time left before everyone leaves. Not a clock per kid. A dashboard can come later.

**v1 launch:** open [kiosk.html](kiosk.html) in Edge or Chrome and leave the tab open. Not a locked kiosk (no Assigned Access, no `--kiosk`). Fullscreen-on-tap / F11 is optional. Wi‑Fi is fine; offline is not required. Still no cloud API.

This pass is planning only. See [PLAN.md](PLAN.md). Weekday times live in [schedule.json](schedule.json). Friday 6:35 → 7:45 is confirmed; Mon–Thu stay labeled placeholders (`6:15` / `7:15`). Do not invent replacements.

When the countdown is not running, the leftover tab should still be useful (clock, a status word, or both — not chosen). The plan also covers the whole-day cycle of a tab that stays open: idle → countdown → leave/late → back to idle (handoff not chosen). No idle UI is locked yet.

The HTML file is a static mock so you can feel the idea, not the full app.
