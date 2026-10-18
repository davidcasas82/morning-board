# morning-board

Household morning countdown for an old Surface Pro. One shared clock — not a clock per kid.

**Launch:** open [https://davidcasas82.github.io/morning-board/](https://davidcasas82.github.io/morning-board/) in Edge or Chrome and leave that tab open. Wi‑Fi is required for the URL, weather, and weekday-time updates. Fullscreen / F11 is optional. Not Assigned Access, not `--kiosk`.

Weekday **alarm** and **on-the-road** times live in [schedule.json](schedule.json). Change the file and push (or ask Grok to). The open tab polls it about once a minute. Downstairs is 15 minutes before on-the-road. Friday 6:35 / 7:45 is confirmed; Mon–Thu `6:15` / `7:15` are still placeholders until you replace them.

The gear on the page is today-only override and countdown preview. It does not write the week.

## Home network host

Port **8780** serves this checkout on the LAN. It is separate from the quotes-only server on 8765. `GET /quotes.json` on 8780 reads `~/Library/Application Support/morning-board/quotes.json`. That file is not in git. Do not put a LAN address in the repo.

The page requests same-origin `/quotes.json`. On GitHub Pages that URL 404s, the quote fetch fails, and the clock stays up. The example config uses that path.

This does not change the frame's start URL. Leave the frame on [the Pages board](https://davidcasas82.github.io/morning-board/) until you change it by hand.

The server reads files from disk, so HTML updates do not need a restart. A second LaunchDaemon runs `git pull --ff-only` every 15 minutes and at boot. If the pull fails, the files already on disk stay.

Both jobs are LaunchDaemons. They can start at boot with no user logged in. Cloning the repo does not install them.

1. Replace `__REPO__` and `__HOME__` in `launchd/com.davidcasas.morningboard.board.plist` and `launchd/com.davidcasas.morningboard.board-pull.plist`.
2. Copy both files to `/Library/LaunchDaemons/` (not `~/Library/LaunchAgents/`).
3. Bootstrap them:

```
sudo launchctl bootstrap system /Library/LaunchDaemons/com.davidcasas.morningboard.board.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.davidcasas.morningboard.board-pull.plist
```

From another machine on the LAN, open `http://<mini>:8780/`. The CSV sync LaunchAgent is unchanged.
