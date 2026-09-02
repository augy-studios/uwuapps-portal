// /lib/uwu-telegram.js
// Shared server side helpers for account linking and two factor authentication.
//
// Everything that touches a credential lives here, so there is one place to
// review: the shared secret check for bot calls, the one time code generator
// and its pepper, the Sign in token, and the Telegram Bot API calls.
//
// The message copy is here too. The portal composes the approval prompt and
// the pushed code message itself, which is the single exception to the rule
// that every outgoing message goes through the bot's own helper, so the same
// style rules apply here: no em dashes, and the product is called the portal
// or UwU Suite, never named after the bot.

import { createHmac, randomBytes, randomInt, timingSafeEqual } from 'node:crypto';

/* --- configuration -------------------------------------------------------- */

export const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || '';
export const BOT_USERNAME = process.env.TELEGRAM_BOT_USERNAME || '';
export const SHARED_SECRET = process.env.TELEGRAM_BOT_SHARED_SECRET || '';
export const CODE_PEPPER = process.env.MFA_CODE_PEPPER || '';
export const PORTAL_ORIGIN = (process.env.PORTAL_ORIGIN || 'https://uwuapps.org').replace(/\/$/, '');

// One config constant, not a literal repeated in three files.
export const MAGIC_PATH = '/api/auth/magic';

export const LINK_CODE_TTL_MINUTES = 10;
export const CHALLENGE_TTL_SECONDS = 120;
export const OTP_TTL_SECONDS = 300;
export const OTP_MAX_ATTEMPTS = 5;
export const RECOVERY_CODE_COUNT = 10;

// Counting the pushes at login and the /code calls together against one budget,
// so neither path is a cheaper way to flood the chat than the other.
export const OTP_BUDGET = { login: { max: 3, windowSeconds: 300 }, unlink: { max: 3, windowSeconds: 900 } };
export const CHALLENGE_BUDGET = { max: 5, windowSeconds: 600 };
export const MAX_POLLS_PER_CHALLENGE = 300;

/* --- small primitives ----------------------------------------------------- */

export function constantTimeEquals(a, b) {
    const left = Buffer.from(String(a ?? ''), 'utf8');
    const right = Buffer.from(String(b ?? ''), 'utf8');
    if (left.length !== right.length) return false;
    return timingSafeEqual(left, right);
}

function hmacHex(key, message) {
    return createHmac('sha256', key).update(message).digest('hex');
}

/** 8 uppercase base32 characters, the shape the bot validates before calling. */
export function generateLinkCode() {
    const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
    let out = '';
    for (let i = 0; i < 8; i++) out += alphabet[randomInt(0, alphabet.length)];
    return out;
}

/** Six digits from a cryptographic source, leading zeros kept. */
export function generateOtp() {
    return String(randomInt(0, 1000000)).padStart(6, '0');
}

/** A two digit number shown on the login page and repeated in the prompt. */
export function generateMatchNumber() {
    return randomInt(10, 100);
}

/**
 * The purpose is part of what is hashed, not a label on the row, so a code
 * issued for an unlink can never satisfy a login and the reverse.
 */
export function hashOtp(purpose, userId, code) {
    if (!CODE_PEPPER) throw { status: 500, message: 'MFA_CODE_PEPPER is not configured' };
    return hmacHex(CODE_PEPPER, `${purpose}:${userId}:${code}`);
}

/** 32 random bytes, base64url. Carries no user id and nothing readable. */
export function generateMagicToken() {
    return randomBytes(32).toString('base64url');
}

export function hashMagicToken(token) {
    if (!CODE_PEPPER) throw { status: 500, message: 'MFA_CODE_PEPPER is not configured' };
    return hmacHex(CODE_PEPPER, `magic:${token}`);
}

export function generateRecoveryCode() {
    // 10 characters, no vowels and no lookalikes, so a code read off a screen
    // and typed back does not turn into a support request.
    const alphabet = '23456789BCDFGHJKMNPQRSTVWXYZ';
    let out = '';
    for (let i = 0; i < 10; i++) out += alphabet[randomInt(0, alphabet.length)];
    return out;
}

export function deepLink(code) {
    return `https://t.me/${BOT_USERNAME}?start=link_${code}`;
}

export function magicUrl(token) {
    return `${PORTAL_ORIGIN}${MAGIC_PATH}?token=${encodeURIComponent(token)}`;
}

export function isoIn(seconds) {
    return new Date(Date.now() + seconds * 1000).toISOString();
}

export function isoAgo(seconds) {
    return new Date(Date.now() - seconds * 1000).toISOString();
}

/* --- request context ------------------------------------------------------ */

export function clientIp(req) {
    const forwarded = req.headers['x-forwarded-for'];
    if (typeof forwarded === 'string' && forwarded) return forwarded.split(',')[0].trim();
    return req.socket?.remoteAddress || '';
}

/** Vercel puts a coarse location on the request. Never more precise than a city. */
export function coarseLocation(req) {
    const city = req.headers['x-vercel-ip-city'];
    const country = req.headers['x-vercel-ip-country'];
    const parts = [city ? decodeURIComponent(String(city)) : '', country ? String(country) : ''];
    return parts.filter(Boolean).join(', ');
}

/* --- bot to portal authentication ----------------------------------------- */

/**
 * Verifies X-Bot-Signature over "<ts>.<nonce>.<body>".
 *
 * The body is re serialised here with JSON.stringify, which reproduces what the
 * bot sent because the bot serialises compactly and uses no key that looks like
 * an array index.
 *
 * The shared secret opens this and nothing else. It does not open `unlink`,
 * which is why a stolen VPS cannot detach an account.
 */
export async function verifyBotRequest(req, supabase) {
    if (!SHARED_SECRET) return { valid: false, reason: 'Bot authentication is not configured' };

    const signature = req.headers['x-bot-signature'];
    const ts = req.headers['x-bot-ts'];
    const nonce = req.headers['x-bot-nonce'];
    if (!signature || !ts || !nonce) return { valid: false, reason: 'Missing bot signature headers' };

    const now = Math.floor(Date.now() / 1000);
    const parsed = parseInt(String(ts), 10);
    if (!Number.isFinite(parsed) || Math.abs(now - parsed) > 60) {
        return { valid: false, reason: 'Request timestamp out of range' };
    }

    const body = typeof req.body === 'string' ? req.body : JSON.stringify(req.body ?? {});
    const expected = hmacHex(SHARED_SECRET, `${ts}.${nonce}.${body}`);
    if (!constantTimeEquals(signature, expected)) {
        return { valid: false, reason: 'Invalid bot signature' };
    }

    // Replay protection, one row per nonce.
    const marker = `bot:${nonce}`;
    const { data: used } = await supabase
        .from('uwu_used_request_tokens')
        .select('token')
        .eq('token', marker)
        .single();
    if (used) return { valid: false, reason: 'Token already used' };

    await supabase.from('uwu_used_request_tokens').insert({
        token: marker,
        session_token: 'telegram-bot',
        used_at: new Date().toISOString()
    });

    return { valid: true, reason: 'OK' };
}

/* --- the Telegram Bot API ------------------------------------------------- */

const API_BASE = () => `https://api.telegram.org/bot${BOT_TOKEN}`;

async function callBotApi(method, payload) {
    if (!BOT_TOKEN) throw { status: 500, message: 'TELEGRAM_BOT_TOKEN is not configured' };
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
        const res = await fetch(`${API_BASE()}/${method}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: controller.signal
        });
        const data = await res.json();
        if (!data.ok) {
            // Never log the payload: a code message body carries live digits.
            console.error('[telegram]', method, 'failed:', data.description);
            return { ok: false, description: data.description };
        }
        return { ok: true, result: data.result };
    } catch (e) {
        console.error('[telegram]', method, 'threw:', e?.name || 'error');
        return { ok: false, description: 'Could not reach Telegram' };
    } finally {
        clearTimeout(timer);
    }
}

export async function sendBotMessage(chatId, text, replyMarkup) {
    return callBotApi('sendMessage', {
        chat_id: chatId,
        text,
        parse_mode: 'HTML',
        // A link preview would make Telegram fetch the Sign in URL, and a bare
        // fetch of that URL must never approve anything anyway.
        link_preview_options: { is_disabled: true },
        ...(replyMarkup ? { reply_markup: replyMarkup } : {})
    });
}

export async function editBotMessage(chatId, messageId, text, replyMarkup) {
    return callBotApi('editMessageText', {
        chat_id: chatId,
        message_id: messageId,
        text,
        parse_mode: 'HTML',
        link_preview_options: { is_disabled: true },
        reply_markup: replyMarkup ?? { inline_keyboard: [] }
    });
}

/**
 * The native copy button, Bot API 8.0 and later. If Telegram rejects it the
 * caller retries without it, because the <code> block in the body is already
 * tap to copy on every client. The button is an improvement on that, never the
 * only way to get the digits.
 */
export function copyButton(label, value) {
    return { text: label, copy_text: { text: value } };
}

/** Send, and fall back to the same message without the copy button. */
export async function sendWithOptionalCopyButton(chatId, text, rows, copyRow) {
    const withCopy = await sendBotMessage(chatId, text, {
        inline_keyboard: [...rows, [copyRow]]
    });
    if (withCopy.ok) return withCopy;
    return sendBotMessage(chatId, text, rows.length ? { inline_keyboard: rows } : undefined);
}

/* --- message copy --------------------------------------------------------- */

export function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

const NEVER_ASKED = 'Nobody from the team will ever ask you for this code.';
const NOT_YOU = 'Nobody from the team will ever ask you to approve a prompt you did not start.';

export const COPY = {
    neverAsked: NEVER_ASKED,

    /**
     * Deliberately specific. A vague prompt trains people to approve reflexively,
     * and the match number is what stops somebody who already has the password
     * from harvesting a reflex approval.
     */
    approvalPrompt({ matchNumber, time, device, ip, location }) {
        const where = [ip, location].filter(Boolean).join(', ');
        return [
            '<b>Approve this sign in?</b>',
            '',
            'Somebody is signing in to UwU Suite with your password.',
            '',
            `Match number: <b>${escapeHtml(matchNumber)}</b>`,
            'Approve only if the login page shows the same number.',
            '',
            `Time: ${escapeHtml(time)}`,
            `Device: ${escapeHtml(device || 'unknown')}`,
            ...(where ? [`From: ${escapeHtml(where)}`] : []),
            '',
            NOT_YOU
        ].join('\n');
    },

    approvalButtons(challengeId) {
        return [[
            { text: 'Approve', callback_data: `mfa:a:${challengeId}` },
            { text: 'This was not me', callback_data: `mfa:d:${challengeId}` }
        ]];
    },

    /**
     * A separate message from the prompt, on purpose. The prompt is a decision,
     * this is a credential, and mixing them would put a copy button next to a
     * deny button.
     */
    codeMessage({ code, minutes }) {
        return [
            '<b>Your sign in code</b>',
            '',
            `<code>${escapeHtml(code)}</code>`,
            '',
            `It works for the next ${minutes} minutes. Type it into the page that is waiting for it, or use the button.`,
            '',
            NEVER_ASKED
        ].join('\n');
    },

    unlinkCodeMessage({ code, minutes }) {
        return [
            '<b>Confirm removing the Telegram link</b>',
            '',
            `<code>${escapeHtml(code)}</code>`,
            '',
            `This code confirms removing the link between this chat and your portal account. It is not a sign in. It works for the next ${minutes} minutes.`,
            '',
            'If you did not start this, ignore the message and change your password.',
            '',
            NEVER_ASKED
        ].join('\n');
    },

    codeSuperseded: 'That code is no longer valid.',
    promptApproved: 'Sign in approved. The page that was waiting will continue on its own.',
    promptDenied: [
        'That sign in was stopped and every session for the account has been signed out.',
        '',
        'Somebody had the password, so change it now on the portal.'
    ].join('\n'),
    promptExpired: 'That sign in request expired. Start it again if it was you.'
};
