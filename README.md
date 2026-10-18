# morning-board

Household morning countdown for an old Surface Pro. One shared clock — not a clock per kid.

**Launch:** open [https://davidcasas82.github.io/morning-board/](https://davidcasas82.github.io/morning-board/) in Edge or Chrome and leave that tab open. Wi‑Fi is required for the URL, weather, and weekday-time updates. Fullscreen / F11 is optional. Not Assigned Access, not `--kiosk`.

Weekday **alarm** and **on-the-road** times live in [schedule.json](schedule.json). Change the file and push (or ask Grok to). The open tab polls it about once a minute. Downstairs is 15 minutes before on-the-road. Friday 6:35 / 7:45 is confirmed; Mon–Thu `6:15` / `7:15` are still placeholders until you replace them.

The gear on the page is today-only override and countdown preview. It does not write the week.

## Home network host

Port **8780** serves the board's own files on the LAN: `index.html`, `kiosk.html`, the `*.js` files, `schedule.json`, `board-config*.json`, `fonts/` and `/quotes.json`. Every other path is a 404. `GET /quotes.json` on 8780 reads `~/Library/Application Support/morning-board/quotes.json`. That file is not in git. Do not put a LAN address in the repo.

The page requests same-origin `/quotes.json`. On GitHub Pages that URL 404s, the quote fetch fails, and the clock stays up. The example config uses that path.

This does not change the frame's start URL. Leave the frame on [the Pages board](https://davidcasas82.github.io/morning-board/) until you change it by hand.

The server reads files from disk, so HTML updates do not need a restart. A second LaunchAgent runs `git pull --ff-only` every 15 minutes and at login. If the pull fails, the files already on disk stay.

Both jobs are user LaunchAgents in `~/Library/LaunchAgents/`. A user agent starts after login, not at the boot chime, so auto-login for `casasmacmini-server` is a dependency. Install does not use sudo. Cloning the repo does not install them.

1. Replace `__REPO__` and `__HOME__` in `launchd/com.davidcasas.morningboard.board.plist` and `launchd/com.davidcasas.morningboard.board-pull.plist`.
2. Copy both files to `~/Library/LaunchAgents/`.
3. As `casasmacmini-server`, bootstrap them:

```
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.davidcasas.morningboard.board.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.davidcasas.morningboard.board-pull.plist
```

From another machine on the LAN, open `http://<mini>:8780/`. The CSV sync LaunchAgent is unchanged.
