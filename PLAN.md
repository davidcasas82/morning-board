# Morning leave-by countdown — plan

Planning pass only. No app, accounts, or live calendars. David can correct times and pick this up locally.

## Problem

After the alarm, the kids sit on their beds and lose track of time. The thing they need is not a dashboard. They need a visual they cannot ignore: **how much time is left before they have to leave the house**.

When the alarm goes off, a countdown starts toward that day’s **leave-by** time. Alarm and leave-by change by weekday. Friday is a later start.

**Confirmed example (Friday 2026-08-21):** alarm **6:35 AM**, leave the house by **7:45 AM** at the latest.

## Product north star

A leftover browser tab on an old Surface Pro (Windows, touch), sitting on a dresser or mounted wall-ish.

- Giant high-contrast remaining-time numbers
- Instant read: “we are running out of time”
- Cheap static page, no accounts, no tracking, no kid photos, no cloud API
- v1 is the clock. A thin dashboard around it is later, not now.

**Locked for v1: one shared household clock.** Not a clock per kid. The number on the tablet is the family’s remaining time to leave, even though the kids go to different schools.

**Locked for v1: normal browser tab.** Open `kiosk.html` (or the page) in Edge or Chrome and leave that tab open. Not a locked kiosk: no Assigned Access, no `--kiosk` flag, no “this tablet is only this page.”

## v1 vs later

| Now (v1) | Later (setup or v2+), only if v1 is used |
| --- | --- |
| One shared countdown for the house | Optional thin chrome: weekday, a couple of getting-ready steps |
| Weekday schedule in a data file | Exception dates (minimum day, no school, late start not on Friday) |
| Adult override for *today’s* leave-by | Sound at alarm or at zero (easy to hate; keep optional) |
| Big remaining time, urgency colors, zero / late state | Stay-awake and auto-start-on-login (tablet setup, not product) |
| Open the page in Edge/Chrome and leave the tab open | — |

Out of scope: locked kiosk mode, per-kid countdowns, backends, logins, Gmail, Google Calendar, school-portal scrapers, paid APIs, weather, location, photos.

## How a day is defined

A school morning is three facts plus a label:

1. **`alarm`** — when the countdown *starts* (the wake-up). Before this, the page is idle / waiting, not counting down to leave-by.
2. **`leaveBy`** — when they must be out the door. This is a house time, not a school bell.
3. **`enabled`** — school morning or not. Weekends start as off.
4. **`label`** — short human note (“Late start Friday”), for adults and for a future chrome line. Not required for the giant numbers.

Times in data are 24-hour `HH:MM` in the household timezone (`America/Los_Angeles` / Irvine). The UI can show `7:45 AM`.

Leave-by already includes the drive / walk / drop-off buffer. **Do not store school start bells** unless we later need them. We do not know those bells and should not invent them.

### One shared clock (locked)

Family context (for planning, not for on-screen copy): Daniel and Leah at Legacy Magnet Academy, Elena at Ladera Elementary. Two sites could imply two “must leave” times. **That does not change v1.** There is still one leave-by for the house: the time everyone needs to be out the door.

- Data shape: one `alarm` and one `leaveBy` per weekday. No `kids[]`, no per-name times.
- On-screen: one giant number. No kid names, no school names, no stacked clocks.
- If two schools conflict, pick the household leave-by that still gets everyone there. Fold that into the weekday time; do not split the UI.

Do not design per-kid countdowns in this pass or the next build.

## Schedule data

Source of truth: [`schedule.json`](schedule.json).

- Weekday keys, not calendar dates.
- Friday `06:35` / `07:45` is **confirmed** from the 2026-08-21 example.
- Mon–Thu stay **labeled placeholders** (`06:15` / `07:15`, earlier than Friday only because Friday is the late start). They are not school bells and they are not measured leave-by times. **Do not invent replacements.**
- Sat–Sun are off. No weekend leave-by invented.

What can wait (does not block this plan):

- Real Mon–Thu `alarm` and `leaveBy` when David has them. Keep the placeholder numbers until then.
- Any weekly exception beyond “Friday is later” (minimum days, early Wednesday, etc.).
- Weekend / no-school copy, once David picks an idle option.

Edit the JSON; do not hide times in the HTML.

## UX

### What the kids see

The leftover tab. Near-black background. One number they can read from the bed. Designed to fill the viewport; **fullscreen-on-tap / F11 is optional, not required.**

- **When the leave-by countdown is not running** (before the alarm, weekend, no-school day): the leftover tab should still be useful — not blank or dark. Options below; not a chosen UI yet.
- **Alarm → leave-by:** giant remaining time. Format as **total minutes:seconds** (`70:00`, then `69:59`). Kids think in minutes. `1:10:00` is weaker.
- **At 0:** the number is no longer the point. Big **LEAVE NOW** (or equivalent). High contrast, hard to ignore.
- **After 0:** stay on the leave state and show overtime (`+2:15 late`). Do not reset, do not play a cheerful “done.” They are late.

A small secondary line is allowed if it does not steal the number: `Fri · leave by 7:45`. Getting-ready steps and extra chrome wait for v2.

### Urgency as time runs down

The number should feel more dangerous as the window closes. Suggested bands (tune after a real morning):

| Remaining | Feel |
| --- | --- |
| More than 20 minutes | High-contrast white / pale |
| 20–10 minutes | Amber |
| 10–5 minutes | Orange-red |
| Under 5 minutes | Bright red, gentle pulse |
| Zero / late | Flashing red + black, “LEAVE NOW” |

No kid photos. No points, streaks, or “who got ready first.”

### Idle / off-countdown (open — David picks)

When the shared leave-by countdown is **not** running, the Surface tab should still be useful. Not a black screen. Not a finished idle UI in this plan — only options to choose from on review.

Applies to: before today’s alarm, weekends, and other `enabled: false` days. Does **not** replace the running countdown, the zero **LEAVE NOW** state, or overtime.

| Option | What they’d see | Notes |
| --- | --- | --- |
| **A) Regular clock** | Current time, large | Same “one big number” habit as the countdown. No status copy. |
| **B) A status word** | e.g. weekend / no school / waiting | Tells them why it isn’t counting. No clock. Exact words not locked. |
| **C) Both** | Large current time + a small status word | Clock to glance at; word for why it’s idle. |

Open for David to pick when he reviews this PR. Do not pick here. Do not spec type, color, or layout until he chooses.

### What an adult does (touch)

Kids should not have to aim. Adults need fat targets, no hover-only UI.

- **Open the page and leave it** — that is the v1 launch path. Fullscreen tap / F11 is a nice extra, not the product.
- **Quiet adult affordance** — a corner control (gear). Large hit area, low visual weight so it does not look like a game button.
- **Today override** — set *today’s* leave-by without editing the weekday file (dentist, traffic, “we’re already late, leave by 7:20”). Reset returns to the weekday default.
- **Mock-only jumps** — “5 minutes left” and “hit zero” so David can preview urgency on the Surface at 2pm.

Today’s override is session-only in the mock. v1 can keep it in `localStorage` so a refresh does not lose it; still not an account.

## Surface Pro: product vs later setup

**v1 launch path (locked):** copy or clone the files, open `kiosk.html` (or the page) in Edge or Chrome, leave the tab open. Wi‑Fi on the Surface is fine. Offline is **not** a v1 requirement. Still no cloud API — v1 stays a static local page that does not need Gmail, calendars, or a hosted backend.

**Not v1 (later setup, not product):**

- Stay-awake / “don’t dim”
- Auto-start on login
- Windows version specifics
- Assigned Access, `--kiosk`, or any “this tablet is only this page” lock

Do not turn those into v1 requirements. A leftover browser tab is enough.

**Display (when we get there)**

- Size the number in viewport units (`vmin`) so Windows scaling does not clip it, including with the browser chrome still showing.
- High contrast beats brand color. Red is for *running out of time*, not for the whole morning.
- Touch targets ≥ 48px. No hover menus.

## Open questions for David

Already decided (not listed below): **one shared clock**; **normal leftover browser tab** (not a locked kiosk); **Wi‑Fi is fine** (offline is not a v1 requirement).

Still unknown — placeholders are fine; do not block the plan:

1. **Mon–Thu leave-by and alarm** — the only confirmed pair is Friday 6:35 → 7:45. Keep `06:15` / `07:15` labeled as placeholders. Do not invent new times.
2. **Sound at zero?** — default no.
3. **Where the tablet lives** — dresser vs hallway vs by the door (how huge the type needs to be).
4. **Who edits the JSON?** — David only is fine for v1.

Stay-awake, auto-start, and Windows version are later setup. Not open product questions.

## Recommended build path (after you review)

1. Leave Mon–Thu in `schedule.json` as labeled placeholders until real times exist. Do not invent replacements.
2. Use `kiosk.html` as the v1 shell: one shared clock, static page, no framework. Open it in a normal tab.
3. Drive the clock from the JSON + the real local clock. Keep the Friday demo mode as a preview switch.
4. Persist today’s leave-by override in `localStorage`.
5. Confirm waiting-before-alarm and late states on a real morning.
6. Only later, if needed: stay-awake and launch-on-login. Dashboard strip after the clock is in use. Still no locked kiosk unless someone asks.

Do not add a build step, a framework, calendar OAuth, or per-kid UI.

## Assumptions (labeled)

- Irvine / Pacific time (`America/Los_Angeles`).
- **One shared household clock** is a locked v1 decision. Not per kid.
- **v1 launch** is a leftover Edge/Chrome tab. Not Assigned Access, not `--kiosk`.
- Fullscreen-on-tap / F11 is optional.
- Wi‑Fi is available. Offline is not a v1 requirement. Still no cloud API; the page is static/local.
- Friday 6:35 / 7:45 is a real household example, not a school-bell time.
- Mon–Thu placeholders stay **`06:15` / `07:15`**, labeled as guesses. **Do not invent new times.**
- Weekends are off. No Saturday sports leave-by invented.
- On-screen copy does not use kid names or school names. Those stay in this plan and in JSON comments.
- School start bells for Legacy Magnet Academy and Ladera Elementary were **not** looked up and are **not** stored. Leave-by is the only time that matters for v1.
- Stay-awake and auto-start-on-login are later setup, not v1.
- The mock defaults to a **Friday demo** (pretend it is 6:35) so opening it at 2pm still shows a countdown. A “use the real clock” switch is there for an actual morning.
