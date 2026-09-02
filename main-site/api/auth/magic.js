// api/auth/magic.js, the Sign in button on the pushed code message.
//
// A browser navigation rather than a JSON action, which is why it lives here
// rather than inside api/telegram.js. Neither method accepts the shared secret.
//
// A GET must never approve anything. Telegram, link scanners, corporate mail
// filters and antivirus proxies all fetch URLs they see with no human involved,
// so a bare fetch of this URL is inert: GET renders a confirmation page and
// changes no state, POST approves or denies.
//
// The confirmation page is also where the match number defence survives. A one
// tap approval is exactly the reflex approval the match number exists to stop,
// so the page repeats everything the prompt said.

import { supabase } from '../_supabase.js';
import { COPY, constantTimeEquals, hashMagicToken } from '../../lib/uwu-telegram.js';
import {
    clearChallengeMessages,
    createSession,
    killAllSessions,
    loadChallenge,
    markChallenge,
    userById
} from '../_mfa.js';

export default async function handler(req, res) {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Content-Type', 'text/html; charset=utf-8');

    if (req.method !== 'GET' && req.method !== 'POST') {
        return res.status(405).send(expiredPage());
    }

    const token = req.method === 'GET'
        ? String(req.query?.token || '')
        : String(req.body?.token || '');

    const challenge = await challengeForToken(token);

    // Unknown, expired, already resolved or already used all get the same plain
    // page. Never a stack trace, and never a silent redirect with no explanation.
    if (!challenge) return res.status(410).send(expiredPage());

    if (req.method === 'GET') {
        return res.status(200).send(confirmPage(challenge, token));
    }

    const decision = String(req.body?.decision || '');
    if (!['approve', 'deny'].includes(decision)) {
        return res.status(400).send(expiredPage());
    }

    // Single use, marked in the same step that resolves the challenge.
    const { data: claimed } = await supabase
        .from('uwusuite_mfa_challenges')
        .update({ magic_used_at: new Date().toISOString() })
        .eq('id', challenge.id)
        .is('magic_used_at', null)
        .select('id');

    if (!claimed?.length) return res.status(410).send(expiredPage());

    const resolved = await markChallenge(
        challenge.id, decision === 'approve' ? 'approved' : 'denied'
    );
    if (!resolved) return res.status(410).send(expiredPage());

    if (decision === 'deny') {
        await killAllSessions(challenge.user_id);
        await clearChallengeMessages(challenge.telegram_id, challenge, COPY.promptDenied);
        return res.status(200).send(deniedPage());
    }

    await clearChallengeMessages(challenge.telegram_id, challenge, COPY.promptApproved);

    // The tap usually lands on a different device than the login. The waiting
    // browser picks up its session on its next poll, and nothing about that poll
    // changes. This session is for the browser that opened the link, offered as
    // Continue here for the common case where they are the same phone.
    let handoff = null;
    try {
        const user = await userById(challenge.user_id);
        if (user) handoff = await createSession(user);
    } catch {
        console.error('[magic] could not mint the continue here session');
    }

    return res.status(200).send(approvedPage(handoff));
}

/**
 * The token carries no user id, no username and nothing readable. It is a
 * lookup key, and only its HMAC is ever stored. It is never written to a log
 * or an audit row on either host.
 */
async function challengeForToken(token) {
    if (!token || token.length < 32 || token.length > 128) return null;

    let hash;
    try {
        hash = hashMagicToken(token);
    } catch {
        return null;
    }

    const { data } = await supabase
        .from('uwusuite_mfa_challenges')
        .select('id')
        .eq('magic_token_hash', hash)
        .is('magic_used_at', null)
        .limit(1);

    const found = data?.[0];
    if (!found) return null;

    const challenge = await loadChallenge(found.id);
    if (!challenge) return null;
    if (!constantTimeEquals(challenge.magic_token_hash || '', hash)) return null;
    if (challenge.status !== 'pending') return null;
    if (challenge.magic_used_at) return null;

    return challenge;
}

/* --- pages ---------------------------------------------------------------- */

function esc(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

const STYLE = `
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 1.5rem;
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background: #f6f5f9; color: #1b1a20; }
.card { width: min(30rem, 100%); background: #fff; border: 1px solid #e5e2ee;
  border-radius: 18px; padding: 1.75rem; box-shadow: 0 18px 40px rgba(30, 22, 60, .08); }
h1 { font-size: 1.25rem; margin: 0 0 .5rem; }
p { margin: 0 0 1rem; }
.match { font-size: 3rem; font-weight: 700; letter-spacing: .08em; text-align: center;
  margin: 1rem 0 .25rem; }
.match-note { text-align: center; color: #6c6880; font-size: .85rem; margin-bottom: 1.25rem; }
dl { margin: 0 0 1.5rem; display: grid; grid-template-columns: auto 1fr; gap: .35rem 1rem;
  font-size: .9rem; }
dt { color: #6c6880; }
dd { margin: 0; word-break: break-word; }
button, .btn { width: 100%; padding: .8rem 1rem; border-radius: 12px; font: inherit;
  font-weight: 600; cursor: pointer; border: 1px solid transparent; display: block;
  text-align: center; text-decoration: none; }
.primary { background: #6d4aff; color: #fff; }
.secondary { background: transparent; color: #b23c4b; border-color: #e8ccd2; margin-top: .6rem; }
.muted { color: #6c6880; font-size: .85rem; }
@media (prefers-color-scheme: dark) {
  body { background: #131218; color: #efeef5; }
  .card { background: #1c1b23; border-color: #302e3c; box-shadow: none; }
  dt, .match-note, .muted { color: #a09cb4; }
  .secondary { color: #ff8a9b; border-color: #46323a; }
}`;

function page(title, body) {
    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="robots" content="noindex, nofollow" />
<title>${esc(title)}</title>
<style>${STYLE}</style>
</head>
<body><main class="card">${body}</main></body>
</html>`;
}

function confirmPage(challenge, token) {
    const when = new Date(challenge.created_at).toUTCString();
    return page('Confirm this sign in', `
<h1>Is this you signing in?</h1>
<p>Approve only if the number below matches the one on the page you are signing in from.</p>
<div class="match">${esc(challenge.match_number)}</div>
<p class="match-note">Match number</p>
<dl>
  <dt>Time</dt><dd>${esc(when)}</dd>
  <dt>Device</dt><dd>${esc(challenge.device || 'unknown')}</dd>
  ${challenge.ip ? `<dt>From</dt><dd>${esc(challenge.ip)}</dd>` : ''}
</dl>
<form method="post">
  <input type="hidden" name="token" value="${esc(token)}" />
  <button class="primary" name="decision" value="approve" type="submit">Yes, this was me</button>
  <button class="secondary" name="decision" value="deny" type="submit">No, this was not me</button>
</form>`);
}

function approvedPage(handoff) {
    let continueHere = '';
    if (handoff) {
        // A display name is user supplied, so the JSON is escaped for a script
        // context and not only for JSON, or a name could close the tag early.
        const payload = JSON.stringify(JSON.stringify({
            token: handoff.token,
            expiresAt: handoff.expiresAt,
            user: handoff.user
        })).replace(/</g, '\\u003C');

        continueHere = `<a class="btn primary" id="continueHere" href="/">Continue here</a>
<script>
  document.getElementById('continueHere').addEventListener('click', function () {
    try { localStorage.setItem('uwusuite_session', ${payload}); } catch (e) {}
  });
</script>`;
    }

    return page('Approved', `
<h1>Approved</h1>
<p>The sign in on the other device can continue now. You can close this page.</p>
${continueHere}
<p class="muted" style="margin-top:1rem">Signing in on this device instead? Use the button above.</p>`);
}

function deniedPage() {
    return page('Stopped', `
<h1>That sign in was stopped</h1>
<p>Every session for the account has been signed out.</p>
<p>Somebody else has your password. Change it now.</p>
<a class="btn primary" href="/">Go to the portal</a>`);
}

function expiredPage() {
    return page('No longer valid', `
<h1>That request is no longer valid</h1>
<p>It may have expired, been approved already, or been opened twice. Nothing has been changed.</p>
<a class="btn primary" href="/">Start a fresh sign in</a>`);
}
