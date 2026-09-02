// api/telegram.js
// POST /api/telegram, body: { action, ...params }
//
// Two kinds of caller, and they never overlap:
//
//   Web actions   the signed in browser, existing bearer session, exactly like
//                 every other authenticated call.
//   Bot actions   the VPS, X-Bot-Signature over the body with the shared
//                 secret plus a timestamp and a nonce.
//
// The shared secret opens the bot actions and nothing else. In particular it
// does not open `unlink`, so the bot has no code path to detaching an account
// even by accident. That is a property of this routing table, not a convention
// somebody has to remember.
//
// The app_ actions are the same idea. The secret alone never authorises a
// write: every one of them resolves the Telegram id to a linked account first,
// and the account's own role decides what happens, exactly as it would in the
// Admin Panel. So a stolen VPS gets whatever the accounts linked to it could
// already do from a browser, and nothing more.

import { supabase, resolveSession, verifyPassword, ok, err, cors } from './_supabase.js';
import {
    COPY,
    LINK_CODE_TTL_MINUTES,
    OTP_TTL_SECONDS,
    deepLink,
    generateLinkCode,
    isoIn,
    sendWithOptionalCopyButton,
    verifyBotRequest
} from '../lib/uwu-telegram.js';
import {
    clearChallengeMessages,
    countRecoveryCodes,
    createChallenge,
    issueOtp,
    killAllSessions,
    linkForTelegramId,
    linkForUser,
    loadChallenge,
    markChallenge,
    mintRecoveryCodes,
    userById,
    verifyOtp
} from './_mfa.js';
import { buildCreate, buildPatch } from '../lib/uwu-apps.js';

const BOT_ACTIONS = new Set([
    'redeem',
    'lookup',
    'mfa_resolve',
    'mfa_issue_code',
    'app_list',
    'app_create',
    'app_update',
    'app_delete'
]);

const WEB_ACTIONS = new Set([
    'status',
    'issue_code',
    'unlink_request',
    'unlink',
    'mfa_enroll',
    'mfa_enroll_status',
    'mfa_disable',
    'mfa_regenerate_recovery'
]);

export default async function handler(req, res) {
    if (cors(req, res)) return;
    if (req.method !== 'POST') {
        return res.status(405).json({ ok: false, error: 'Method not allowed' });
    }

    const { action } = req.body || {};

    let user = null;
    if (BOT_ACTIONS.has(action)) {
        const sig = await verifyBotRequest(req, supabase);
        if (!sig.valid) return res.status(403).json({ ok: false, error: sig.reason });
    } else if (WEB_ACTIONS.has(action)) {
        user = await resolveSession(req);
        if (!user) return res.status(401).json({ ok: false, error: 'Not authenticated' });
    } else {
        return res.status(400).json({ ok: false, error: `Unknown action: ${action}` });
    }

    try {
        switch (action) {

            /* --- the Settings tab ----------------------------------------- */

            case 'status': {
                const link = await linkForUser(user.id);
                if (!link) return ok(res, { linked: false, mfaEnabled: false });

                const { data: recent } = await supabase
                    .from('uwusuite_mfa_challenges')
                    .select('status, device, ip, created_at, resolved_at')
                    .eq('user_id', user.id)
                    .neq('status', 'pending')
                    .order('created_at', { ascending: false })
                    .limit(5);

                return ok(res, {
                    linked: true,
                    telegramUsername: link.telegram_username || null,
                    telegramId: String(link.telegram_id),
                    linkedAt: link.linked_at,
                    mfaEnabled: link.mfa_enabled,
                    mfaEnabledAt: link.mfa_enabled_at,
                    recoveryCodesLeft: link.mfa_enabled ? await countRecoveryCodes(user.id) : 0,
                    recentApprovals: recent || []
                });
            }

            /**
             * Mint a linking code. One live code per user, ten minute TTL.
             * Only a signed in portal session can reach this, which is why the
             * bot can redeem a code but can never create one.
             */
            case 'issue_code': {
                const existing = await linkForUser(user.id);
                if (existing) throw { status: 409, message: 'This account is already linked' };

                await supabase
                    .from('uwusuite_telegram_link_codes')
                    .delete()
                    .eq('user_id', user.id)
                    .is('consumed_at', null);

                const code = generateLinkCode();
                const expiresAt = isoIn(LINK_CODE_TTL_MINUTES * 60);

                const { error } = await supabase
                    .from('uwusuite_telegram_link_codes')
                    .insert({ code, user_id: user.id, expires_at: expiresAt });
                if (error) throw { status: 500, message: 'Could not create a code' };

                return ok(res, { code, expiresAt, deepLink: deepLink(code) });
            }

            /* --- linking, called by the bot ------------------------------- */

            case 'redeem': {
                const submitted = String(req.body.code || '').trim().toUpperCase();
                const telegramId = Number(req.body.telegramId);
                if (!/^[A-Z2-7]{8}$/.test(submitted) || !Number.isFinite(telegramId)) {
                    throw { status: 400, message: 'That code is not valid' };
                }

                // The primary key lookup is exact, and the row is only accepted
                // when it is unconsumed and unexpired, so there is nothing to
                // compare in variable time.
                const { data: row } = await supabase
                    .from('uwusuite_telegram_link_codes')
                    .select('*')
                    .eq('code', submitted)
                    .is('consumed_at', null)
                    .single();

                if (!row) throw { status: 400, message: 'That code is not valid or has been used' };
                if (new Date(row.expires_at) < new Date()) {
                    throw { status: 400, message: 'That code has expired, please make a new one' };
                }

                const alreadyLinked = await linkForTelegramId(telegramId);
                if (alreadyLinked && alreadyLinked.user_id !== row.user_id) {
                    throw { status: 409, message: 'This Telegram account is already linked to another account' };
                }
                if (await linkForUser(row.user_id)) {
                    throw { status: 409, message: 'That account is already linked' };
                }

                const { data: claimed } = await supabase
                    .from('uwusuite_telegram_link_codes')
                    .update({
                        consumed_at: new Date().toISOString(),
                        consumed_by_telegram_id: telegramId
                    })
                    .eq('code', submitted)
                    .is('consumed_at', null)
                    .select('code');

                if (!claimed?.length) {
                    throw { status: 409, message: 'That code was just used somewhere else' };
                }

                const { error: linkErr } = await supabase
                    .from('uwusuite_telegram_links')
                    .insert({
                        user_id: row.user_id,
                        telegram_id: telegramId,
                        telegram_username: String(req.body.telegramUsername || '') || null
                    });
                if (linkErr) throw { status: 500, message: 'Could not save the link' };

                const linked = await userById(row.user_id);
                return ok(res, { user: serialize(linked), linkedAt: new Date().toISOString(), mfaEnabled: false });
            }

            case 'lookup': {
                const telegramId = Number(req.body.telegramId);
                if (!Number.isFinite(telegramId)) throw { status: 400, message: 'telegramId required' };

                const link = await linkForTelegramId(telegramId);
                if (!link) return ok(res, { linked: false });

                const linked = await userById(link.user_id);
                if (!linked) return ok(res, { linked: false });

                return ok(res, {
                    linked: true,
                    user: serialize(linked),
                    linkedAt: link.linked_at,
                    mfaEnabled: link.mfa_enabled
                });
            }

            /* --- unlinking, web only -------------------------------------- */

            /**
             * Sends a confirmation code to the linked chat and returns only its
             * expiry, never the digits. It is a purpose = 'unlink' code, so
             * requesting one never kills a live login code.
             */
            case 'unlink_request': {
                const link = await linkForUser(user.id);
                if (!link) throw { status: 404, message: 'This account is not linked' };
                if (link.mfa_enabled) {
                    throw {
                        status: 409,
                        message: 'Turn two factor authentication off first, then unlink'
                    };
                }

                const issued = await issueOtp(user.id, { purpose: 'unlink', source: 'web' });
                if (issued.rateLimited) {
                    throw { status: 429, message: 'Too many codes requested, please wait a few minutes' };
                }

                const sent = await sendWithOptionalCopyButton(
                    link.telegram_id,
                    COPY.unlinkCodeMessage({
                        code: issued.code,
                        minutes: Math.round(OTP_TTL_SECONDS / 60)
                    }),
                    [],
                    { text: 'Copy the code', copy_text: { text: issued.code } }
                );
                if (!sent.ok) {
                    throw {
                        status: 502,
                        message: 'Could not reach the linked chat. Unlinking needs it to be reachable.'
                    };
                }

                return ok(res, { expiresAt: issued.expiresAt });
            }

            /**
             * The bot cannot reach this action at all. Deleting a link takes a
             * portal session and a code delivered to the chat.
             */
            case 'unlink': {
                const link = await linkForUser(user.id);
                if (!link) throw { status: 404, message: 'This account is not linked' };
                if (link.mfa_enabled) {
                    throw {
                        status: 409,
                        message: 'Turn two factor authentication off first, then unlink'
                    };
                }

                const check = await verifyOtp(user.id, 'unlink', req.body.code);
                if (!check.ok) throw { status: 400, message: check.reason };

                await supabase.from('uwusuite_telegram_links').delete().eq('user_id', user.id);
                await supabase.from('uwusuite_mfa_recovery_codes').delete().eq('user_id', user.id);

                return ok(res, { message: 'Telegram is no longer linked to this account' });
            }

            /* --- two factor authentication, web --------------------------- */

            /**
             * Sends a test approval. The toggle only flips once that test is
             * approved, so nobody can lock themselves out of an account whose
             * chat is broken or blocked.
             */
            case 'mfa_enroll': {
                const link = await linkForUser(user.id);
                if (!link) throw { status: 404, message: 'Link Telegram first' };
                if (link.mfa_enabled) throw { status: 409, message: 'It is already on' };

                const { challenge, matchNumber, delivered } = await createChallenge(
                    user, link, req, { purpose: 'enroll', withCode: false }
                );
                if (!delivered) {
                    throw {
                        status: 502,
                        message: 'Could not reach the linked chat, so it has not been turned on'
                    };
                }

                return ok(res, {
                    challenge_id: challenge.id,
                    match_number: matchNumber,
                    expires_at: challenge.expires_at
                });
            }

            case 'mfa_enroll_status': {
                const challenge = await loadChallenge(req.body.challenge_id);
                if (!challenge || challenge.user_id !== user.id || challenge.purpose !== 'enroll') {
                    throw { status: 404, message: 'That request is no longer valid' };
                }
                if (challenge.status === 'pending') return ok(res, { status: 'pending' });
                if (challenge.status !== 'approved') return ok(res, { status: challenge.status });

                const link = await linkForUser(user.id);
                if (link && !link.mfa_enabled) {
                    await supabase
                        .from('uwusuite_telegram_links')
                        .update({ mfa_enabled: true, mfa_enabled_at: new Date().toISOString() })
                        .eq('user_id', user.id);
                    // Shown exactly once, and only here.
                    const codes = await mintRecoveryCodes(user.id);
                    return ok(res, { status: 'approved', recoveryCodes: codes });
                }
                return ok(res, { status: 'approved' });
            }

            /**
             * Requires the current password, because a hijacked browser session
             * must not be able to quietly remove the second factor.
             */
            case 'mfa_disable': {
                const link = await linkForUser(user.id);
                if (!link?.mfa_enabled) throw { status: 409, message: 'It is already off' };

                const full = await userById(user.id);
                if (!full || !(await verifyPassword(String(req.body.password || ''), full.password_hash))) {
                    throw { status: 401, message: 'That password is not right' };
                }

                await supabase
                    .from('uwusuite_telegram_links')
                    .update({ mfa_enabled: false, mfa_enabled_at: null })
                    .eq('user_id', user.id);
                await supabase.from('uwusuite_mfa_recovery_codes').delete().eq('user_id', user.id);

                return ok(res, { message: 'Two factor authentication is off' });
            }

            case 'mfa_regenerate_recovery': {
                const link = await linkForUser(user.id);
                if (!link?.mfa_enabled) throw { status: 409, message: 'Turn it on first' };

                const full = await userById(user.id);
                if (!full || !(await verifyPassword(String(req.body.password || ''), full.password_hash))) {
                    throw { status: 401, message: 'That password is not right' };
                }

                // Regenerating invalidates every previous code.
                const codes = await mintRecoveryCodes(user.id);
                return ok(res, { recoveryCodes: codes });
            }

            /* --- two factor authentication, bot --------------------------- */

            /**
             * The pressing Telegram id must own the challenge. The challenge id
             * alone is never enough, which is why it is checked here rather than
             * trusted from the caller.
             */
            case 'mfa_resolve': {
                const telegramId = Number(req.body.telegramId);
                const decision = String(req.body.decision || '');
                if (!['approve', 'deny'].includes(decision)) {
                    throw { status: 400, message: 'decision must be approve or deny' };
                }

                const challenge = await loadChallenge(req.body.challengeId);
                if (!challenge) throw { status: 404, message: 'That request is no longer valid' };

                if (Number(challenge.telegram_id) !== telegramId) {
                    console.warn('[telegram] refused a resolution from a Telegram id that does not own the challenge');
                    throw { status: 403, message: 'That request is not yours to approve' };
                }
                if (challenge.status !== 'pending') {
                    throw { status: 409, message: 'That request is no longer valid' };
                }

                const resolved = await markChallenge(
                    challenge.id, decision === 'approve' ? 'approved' : 'denied'
                );
                if (!resolved) throw { status: 409, message: 'That request is no longer valid' };

                if (decision === 'deny') {
                    await killAllSessions(challenge.user_id);
                }
                // The bot edits the prompt it was pressed on. The code message
                // is the portal's, so the portal clears it.
                if (challenge.code_message_id) {
                    await clearChallengeMessages(
                        challenge.telegram_id,
                        { message_id: null, code_message_id: challenge.code_message_id },
                        ''
                    );
                }

                return ok(res, { status: resolved.status });
            }

            /**
             * The portal generates the code, never the bot. The bot only
             * displays what it is handed.
             */
            case 'mfa_issue_code': {
                const telegramId = Number(req.body.telegramId);
                const link = await linkForTelegramId(telegramId);
                if (!link) {
                    throw {
                        status: 404,
                        message: 'That chat is not linked to an account',
                        botCode: 'not_linked'
                    };
                }
                if (!link.mfa_enabled) {
                    throw {
                        status: 400,
                        message: 'Two factor authentication is off for that account',
                        botCode: 'mfa_disabled'
                    };
                }

                const issued = await issueOtp(link.user_id, { purpose: 'login', source: 'command' });
                if (issued.rateLimited) {
                    throw {
                        status: 429,
                        message: 'Too many codes requested, please wait a few minutes',
                        botCode: 'rate_limited'
                    };
                }

                // A code issued here supersedes one pushed a moment ago at login,
                // so the message that carried the old one is edited down and its
                // copy button removed.
                if (issued.superseded) {
                    const { data: live } = await supabase
                        .from('uwusuite_mfa_challenges')
                        .select('id, code_message_id')
                        .eq('user_id', link.user_id)
                        .not('code_message_id', 'is', null)
                        .order('created_at', { ascending: false })
                        .limit(1);
                    const previous = live?.[0];
                    if (previous?.code_message_id) {
                        await clearChallengeMessages(
                            link.telegram_id,
                            { message_id: null, code_message_id: previous.code_message_id },
                            ''
                        );
                        await supabase
                            .from('uwusuite_mfa_challenges')
                            .update({ code_message_id: null })
                            .eq('id', previous.id);
                    }
                }

                return ok(res, {
                    code: issued.code,
                    expiresAt: issued.expiresAt,
                    secondsRemaining: OTP_TTL_SECONDS,
                    supersededPushedCode: !!issued.superseded
                });
            }

            /* --- the app directory, bot ----------------------------------- */

            /**
             * Everything the account may see, drafts included, which is what
             * makes an unpublished app editable from a chat at all. The public
             * GET /api/apps still shows published rows only.
             */
            case 'app_list': {
                const user = await contributorFor(req.body.telegramId);

                const { data, error } = await supabase
                    .from('uwusuite_apps')
                    .select(`
                        id, title, description, url, tags,
                        thumbnail_url, gallery_urls, thumbnail_index,
                        published, sort_order, created_by, created_at, published_date
                    `)
                    .order('sort_order')
                    .order('created_at', { ascending: false });
                if (error) throw error;

                return ok(res, { apps: data || [], user: serialize(user) });
            }

            case 'app_create': {
                const user = await contributorFor(req.body.telegramId);

                const { data, error } = await supabase
                    .from('uwusuite_apps')
                    .insert(buildCreate(req.body.app, user.id))
                    .select()
                    .single();
                if (error) throw error;

                return ok(res, { app: data });
            }

            /**
             * The ownership rule is the one from /api/apps: an editor may only
             * change an app they created, an admin may change any of them.
             */
            case 'app_update': {
                const user = await contributorFor(req.body.telegramId);
                const id = String(req.body.id || '');
                if (!id) throw { status: 400, message: 'id is required' };

                const existing = await appById(id);
                if (!existing) throw { status: 404, message: 'That app is not in the directory', botCode: 'not_found' };
                if (!user.is_admin && existing.created_by !== user.id) {
                    throw {
                        status: 403,
                        message: 'That app belongs to somebody else, so only an admin can change it',
                        botCode: 'not_yours'
                    };
                }

                const { data, error } = await supabase
                    .from('uwusuite_apps')
                    .update(buildPatch(req.body.app, user.id))
                    .eq('id', id)
                    .select()
                    .single();
                if (error) throw error;

                return ok(res, { app: data });
            }

            case 'app_delete': {
                const user = await contributorFor(req.body.telegramId, { adminOnly: true });
                const id = String(req.body.id || '');
                if (!id) throw { status: 400, message: 'id is required' };

                const existing = await appById(id);
                if (!existing) throw { status: 404, message: 'That app is not in the directory', botCode: 'not_found' };

                // Logged before it cascades away, same as the Admin Panel does.
                await supabase.from('uwusuite_app_history').insert({
                    app_id: id,
                    user_id: user.id,
                    event_type: 'deleted',
                    description: `App "${existing.title}" was deleted`
                });

                const { error } = await supabase.from('uwusuite_apps').delete().eq('id', id);
                if (error) throw error;

                return ok(res, { title: existing.title });
            }

            default:
                throw { status: 400, message: `Unknown action: ${action}` };
        }
    } catch (e) {
        // A deliberate machine readable reason for the bot, so it can answer
        // "two factor authentication is off" rather than repeating a raw string.
        // Named botCode so a Postgres error's own `code` can never land here.
        if (e?.botCode) {
            return res.status(e.status || 400).json({ ok: false, error: e.message, code: e.botCode });
        }
        return err(res, e);
    }
}

/**
 * The Telegram id, resolved to the portal account that may act for it.
 *
 * A signature proves the call came from the VPS. It says nothing about who is
 * typing, so this is where that question gets answered, and the answer is the
 * same role check the Admin Panel applies: approved, and an editor or an admin.
 * The machine readable codes let the chat explain the refusal in its own words.
 */
async function contributorFor(telegramId, { adminOnly = false } = {}) {
    const id = Number(telegramId);
    if (!Number.isFinite(id)) throw { status: 400, message: 'telegramId required' };

    const link = await linkForTelegramId(id);
    if (!link) {
        throw { status: 403, message: 'That chat is not linked to an account', botCode: 'not_linked' };
    }

    const user = await userById(link.user_id);
    if (!user || !user.is_approved || (!user.is_editor && !user.is_admin)) {
        throw {
            status: 403,
            message: 'That account is not allowed to manage the directory',
            botCode: 'not_allowed'
        };
    }
    if (adminOnly && !user.is_admin) {
        throw { status: 403, message: 'That is an admin only action', botCode: 'not_admin' };
    }
    return user;
}

async function appById(id) {
    const { data } = await supabase
        .from('uwusuite_apps')
        .select('id, title, created_by, published')
        .eq('id', id)
        .single();
    return data || null;
}

function serialize(user) {
    return {
        id: user.id,
        username: user.username,
        displayName: user.display_name,
        isAdmin: user.is_admin,
        isEditor: user.is_editor,
        isApproved: user.is_approved
    };
}
