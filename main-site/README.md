# pwa-template
Augy Studios PWA sites template
Note: The `/api` folder is meant for Vercel serverless functions. Remove if not required.

## Deploying

Bump `CACHE_VERSION` in `sw.js` on every deploy that changes anything the
worker serves (HTML, CSS, JS, images). The browser compares `sw.js` byte for
byte, so if that file has not changed nobody is told there is a new version,
however much else has moved. Treat forgetting to as a build error.

A new worker never activates on its own. It installs and waits, and
`js/sw-update.js` draws a bar at the top of the page offering Reload or Not
now. Never add `skipWaiting()` or `clients.claim()` outside the `message`
handler in `sw.js`; that turns the prompt back into a silent takeover of a
page somebody may be halfway through using. See `update-bar-spec.md` at the
repo root.
