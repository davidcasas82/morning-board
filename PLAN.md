# Morning leave-by countdown — plan

Planning pass only. No app, accounts, or live calendars. David can correct times and pick this up locally.

## Problem

After the alarm, the kids sit on their beds and lose track of time. The thing they need is not a dashboard. They need a visual they cannot ignore: **how much time is left before they have to leave the house**.

When the alarm goes off, a countdown starts toward that day’s **leave-by** time. Alarm and leave-by change by weekday. Friday is a later start.

**Confirmed example (Friday 2026-08-21):** alarm **6:35 AM**, leave the house by **7:45 AM** at the latest.

## Product north star

A full-screen kiosk on an old Surface Pro (Windows, touch), sitting on a dresser or mounted wall-ish.

- Giant high-contrast remaining-time numbers
- Instant read: “we are running out of time”
- Cheap, offline, no accounts, no tracking, no kid photos
- v1 is the clock. A thin dashboard around it is later, not now.

**Locked for v1: one shared household clock.** Not a clock per kid. The number on the tablet is the family’s remaining time to leave, even though the kids go to different schools.

## v1 vs later

| Now (v1) | Later (v2+), only if v1 is used |
| --- | --- |
| One shared countdown for the house | Optional thin chrome: weekday, a couple of getting-ready steps |
| Weekday schedule in a data file | Exception dates (minimum day, no school, late start not on Friday) |
| Adult override for *today’s* leave-by | Sound at alarm or at zero (easy to hate; keep optional) |
| Full-screen, urgency colors, zero / late state | Auto-launch / assigned-access polish once the tablet details are known |
| Open as a file or a tiny local server | — |

Out of scope: per-kid countdowns, backends, logins, Gmail, Google Calendar, school-portal scrapers, paid APIs, weather, location, photos.

## How a day is defined

A school morning is three facts plus a label:

1. **`alarm`** — when the countdown *starts* (the wake-up). Before this, the kiosk is idle / waiting, not counting down to leave-by.
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
- Mon–Thu are **placeholders** (earlier than Friday, because Friday is the late start). They are not school bells and they are not measured leave-by times.
- Sat–Sun are off. No weekend leave-by invented.

What David still needs to fill in (does not block this plan):

- Real Mon–Thu `alarm` and `leaveBy` (and whether those four days are actually the same). Placeholders stay until then.
- Any weekly exception beyond “Friday is later” (minimum days, early Wednesday, etc.).
- Whether the kiosk should stay dark on weekends or show a simple “no school” screen.

Edit the JSON; do not hide times in the HTML.

## UX

### What the kids see

Full screen. Near-black background. One number they can read from the bed.

- **Before alarm:** dim waiting state. Show today’s leave-by and “countdown starts at 6:35”. Do not burn a 70-minute countdown while they are still supposed to be asleep.
- **Alarm → leave-by:** giant remaining time. Format as **total minutes:seconds** (`70:00`, then `69:59`). Kids think in minutes. `1:10:00` is weaker.
- **At 0:** the number is no longer the point. Full-screen **LEAVE NOW** (or equivalent). High contrast, hard to ignore.
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

### What an adult does (touch)

Kids should not have to aim. Adults need fat targets, no hover-only UI.

- **Tap-to-start / tap for fullscreen** — browsers will not fullscreen themselves. First tap enters fullscreen and starts the demo or the day.
- **Quiet adult affordance** — a corner control (gear). Large hit area, low visual weight so it does not look like a game button.
- **Today override** — set *today’s* leave-by without editing the weekday file (dentist, traffic, “we’re already late, leave by 7:20”). Reset returns to the weekday default.
- **Mock-only jumps** — “5 minutes left” and “hit zero” so David can preview urgency on the Surface at 2pm.

Today’s override is session-only in the mock. v1 can keep it in `localStorage` so a refresh does not lose it; still not an account.

## Surface Pro notes (still unknown — do not block)

We do **not** yet know the tablet’s Windows version, whether it must run fully offline, or which stay-awake / kiosk path will stick. Those are setup details for when David has the Surface in hand. They do not change the product: a static page with one giant clock.

**Known enough to plan**

- Old Surface Pro, Windows, touch, likely on a dresser or wall-ish.
- Cheap stack: plain HTML/CSS/JS. No React, bundler, or Node on the tablet.
- The mock is one file (`kiosk.html`) so it can open from `file://`. Copy it onto the tablet and tap to fullscreen (`F11` if a keyboard is attached).

**Fill in later (placeholders, not requirements)**

- **Windows version** — unknown. Edge and Chrome kiosk flags differ by version; try the browser that is already on the machine.
- **Stay awake** — unknown whether Settings → Power & sleep (plugged in → Never) is enough, or whether it still dims. AC power either way.
- **Kiosk / launch on login** — unknown. Candidates after v1 works: Startup-folder shortcut, Task Scheduler, Edge `--kiosk`, Chrome `--kiosk`, or Windows Assigned Access. Skip until the clock is trusted.
- **Offline** — treat “works as a local file / local network, no cloud API” as the default. Confirm on the tablet whether it has reliable Wi‑Fi or should stay file-only.

**Display (when we get there)**

- Size the number in viewport units (`vmin`) so Windows scaling does not clip it.
- High contrast beats brand color. Red is for *running out of time*, not for the whole morning.
- Touch targets ≥ 48px. No hover menus.

## Open questions for David

Decided: **one shared clock.** Not listed below.

Still unknown — placeholders are fine; do not block the plan:

1. **Mon–Thu leave-by and alarm** — the only confirmed pair is Friday 6:35 → 7:45. What are the other school mornings? Are Mon–Thu the same as each other?
2. **Surface Pro facts** — Windows version, stay-awake behavior, whether it must run with no Wi‑Fi, which browser is already installed.
3. **No-school weekday** — dark, “no school”, or hide the tablet.
4. **Sound at zero?** — default no.
5. **Where the tablet lives** — dresser vs hallway vs by the door (how huge the type needs to be).
6. **Who edits the JSON?** — David only is fine for v1.

## Recommended build path (after you review)

1. Correct `schedule.json` Mon–Thu when you know the times. Leave placeholders until then.
2. Use `kiosk.html` as the v1 shell: one shared clock, static page, no framework.
3. Drive the clock from the JSON + the real local clock. Keep the Friday demo mode as a preview switch.
4. Persist today’s leave-by override in `localStorage`.
5. Confirm waiting-before-alarm and late states on a real morning.
6. Only then: stay-awake / kiosk / launch-on-login, using whatever Windows version is on the tablet. Dashboard strip after the clock is in use.

Do not add a build step, a framework, calendar OAuth, or per-kid UI.

## Assumptions (labeled)

- Irvine / Pacific time (`America/Los_Angeles`).
- **One shared household clock** is a locked v1 decision. Not per kid.
- Friday 6:35 / 7:45 is a real household example, not a school-bell time.
- Mon–Thu placeholders are **earlier than Friday** (`06:15` / `07:15`) only because Friday is the late start. They are guesses for the file to be complete. **Replace them.**
- Weekends are off. No Saturday sports leave-by invented.
- On-screen copy does not use kid names or school names. Those stay in this plan and in JSON comments.
- School start bells for Legacy Magnet Academy and Ladera Elementary were **not** looked up and are **not** stored. Leave-by is the only time that matters for v1.
- Surface Windows version, stay-awake, kiosk mode, and offline-or-not are **unknown**. The mock assumes a local HTML file is enough until proven otherwise.
- The mock defaults to a **Friday demo** (pretend it is 6:35) so opening it at 2pm still shows a countdown. A “use the real clock” switch is there for an actual morning.
