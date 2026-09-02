// api/_mfa.js
// The internals both api/auth.js and api/telegram.js share.
//
// One code generator, one invalidation rule, one place to change the expiry.
// The code pushed at login is not a separate action: the login case calls the
// same issueOtp that mfa_issue_code wraps, with source set to 'login'.

import {
    supabase,
    hashPassword,
    verifyPassword,
    generateSessionToken,
    serializeUser,
    detectDevice
} from './_supabase.js';
import {
    CHALLENGE_BUDGET,
    CHALLENGE_TTL_SECONDS,
    COPY,
    OTP_BUDGET,
    OTP_MAX_ATTEMPTS,
    OTP_TTL_SECONDS,
    RECOVERY_CODE_COUNT,
    clientIp,
    coarseLocation,
    constantTimeEquals,
    editBotMessage,
    generateMagicToken,
    generateMatchNumber,
    generateOtp,
    generateRecoveryCode,
    hashMagicToken,
    hashOtp,
    isoAgo,
    isoIn,
    magicUrl,
    sendBotMessage,
    sendWithOptionalCopyButton
} from '../lib/uwu-telegram.js';

/* --- links ---------------------------------------------------------------- */

export async function linkForUser(userId) {
    const { data } = await supabase
        .from('uwusuite_telegram_links')
        .select('*')
        .eq('user_id', userId)
        .single();
    return data || null;
}

export async function linkForTelegramId(telegramId) {
    const { data } = await supabase
        .from('uwusuite_telegram_links')
        .select('*')
        .eq('telegram_id', telegramId)
        .single();
    return data || null;
}

export async function userById(userId) {
    const { data } = await supabase
        .from('uwusuite_users')
        .select('id, username, display_name, email, is_admin, is_editor, is_approved, avatar_url, password_hash')
        .eq('id', userId)
        .single();
    return data || null;
}

/* --- one time codes ------------------------------------------------------- */

/**
 * Issue a code for one account and one purpose.
 *
 * Requesting a new code invalidates the previous one of the same purpose, so
 * there is never more than one live code per account per purpose. A login code
 * and an unlink confirmation code coexist, each invalidating only its own
 * predecessor.
 *
 * Returns { code, expiresAt, superseded, supersededMessage } or
 * { rateLimited: true } when the account has spent its budget. A login that
 * hits the cap still creates the challenge, so the caller decides what to do.
 */
export async function issueOtp(userId, { purpose = 'login', source = 'command' } = {}) {
    const budget = OTP_BUDGET[purpose] || OTP_BUDGET.login;

    const { count } = await supabase
        .from('uwusuite_mfa_otps')
        .select('id', { count: 'exact', head: true })
        .eq('user_id', userId)
        .eq('purpose', purpose)
        .gte('created_at', isoAgo(budget.windowSeconds));

    if ((count || 0) >= budget.max) {
        return { rateLimited: true };
    }

    // Kill any live code of this purpose before minting the next one.
    const { data: superseded } = await supabase
        .from('uwusuite_mfa_otps')
        .select('id')
        .eq('user_id', userId)
        .eq('purpose', purpose)
        .is('used_at', null)
        .gt('expires_at', new Date().toISOString());

    if (superseded?.length) {
        await supabase
            .from('uwusuite_mfa_otps')
            .update({ used_at: new Date().toISOString() })
            .in('id', superseded.map(row => row.id));
    }

    const code = generateOtp();
    const expiresAt = isoIn(OTP_TTL_SECONDS);

    const { error } = await supabase.from('uwusuite_mfa_otps').insert({
        user_id: userId,
        code_hash: hashOtp(purpose, userId, code),
        purpose,
        source,
        expires_at: expiresAt
    });
    if (error) throw { status: 500, message: 'Could not issue a code' };

    return { code, expiresAt, superseded: (superseded?.length || 0) > 0 };
}

/**
 * Verify a code. The purpose is part of what is verified, not a label, so a
 * code issued for an unlink can never satisfy a login.
 *
 * Five wrong attempts kill the code, not just the attempt.
 */
export async function verifyOtp(userId, purpose, code) {
    const digits = String(code || '').replace(/\D/g, '');
    if (digits.length !== 6) return { ok: false, reason: 'That code is not six digits' };

    const { data: row } = await supabase
        .from('uwusuite_mfa_otps')
        .select('*')
        .eq('user_id', userId)
        .eq('purpose', purpose)
        .is('used_at', null)
        .order('created_at', { ascending: false })
        .limit(1)
        .single();

    if (!row) return { ok: false, reason: 'No code is waiting. Request a new one.' };

    if (new Date(row.expires_at) < new Date()) {
        await supabase.from('uwusuite_mfa_otps')
            .update({ used_at: new Date().toISOString() }).eq('id', row.id);
        return { ok: false, reason: 'That code expired. Request a new one.' };
    }

    if (row.attempts >= OTP_MAX_ATTEMPTS) {
        await supabase.from('uwusuite_mfa_otps')
            .update({ used_at: new Date().toISOString() }).eq('id', row.id);
        return { ok: false, reason: 'Too many wrong attempts. Request a new code.' };
    }

    if (!constantTimeEquals(hashOtp(purpose, userId, digits), row.code_hash)) {
        const attempts = row.attempts + 1;
        const patch = { attempts };
        if (attempts >= OTP_MAX_ATTEMPTS) patch.used_at = new Date().toISOString();
        await supabase.from('uwusuite_mfa_otps').update(patch).eq('id', row.id);
        return {
            ok: false,
            reason: attempts >= OTP_MAX_ATTEMPTS
                ? 'Too many wrong attempts. Request a new code.'
                : 'That code is not right.'
        };
    }

    await supabase.from('uwusuite_mfa_otps')
        .update({ used_at: new Date().toISOString() }).eq('id', row.id);
    return { ok: true };
}

/* --- recovery codes ------------------------------------------------------- */

export async function mintRecoveryCodes(userId) {
    await supabase.from('uwusuite_mfa_recovery_codes').delete().eq('user_id', userId);

    const codes = Array.from({ length: RECOVERY_CODE_COUNT }, generateRecoveryCode);
    const rows = await Promise.all(
        codes.map(async code => ({ user_id: userId, code_hash: await hashPassword(code) }))
    );
    const { error } = await supabase.from('uwusuite_mfa_recovery_codes').insert(rows);
    if (error) throw { status: 500, message: 'Could not create recovery codes' };
    return codes;
}

export async function countRecoveryCodes(userId) {
    const { count } = await supabase
        .from('uwusuite_mfa_recovery_codes')
        .select('id', { count: 'exact', head: true })
        .eq('user_id', userId)
        .is('used_at', null);
    return count || 0;
}

/** Single use, consumed atomically enough that a race cannot spend one twice. */
export async function consumeRecoveryCode(userId, submitted) {
    const cleaned = String(submitted || '').replace(/[\s-]/g, '').toUpperCase();
    if (cleaned.length !== 10) return false;

    const { data: rows } = await supabase
        .from('uwusuite_mfa_recovery_codes')
        .select('id, code_hash')
        .eq('user_id', userId)
        .is('used_at', null);

    for (const row of rows || []) {
        if (await verifyPassword(cleaned, row.code_hash)) {
            const { data: claimed } = await supabase
                .from('uwusuite_mfa_recovery_codes')
                .update({ used_at: new Date().toISOString() })
                .eq('id', row.id)
                .is('used_at', null)
                .select('id');
            return !!claimed?.length;
        }
    }
    return false;
}

/* --- challenges ----------------------------------------------------------- */

export async function expireStaleChallenges(userId) {
    await supabase
        .from('uwusuite_mfa_challenges')
        .update({ status: 'expired', resolved_at: new Date().toISOString() })
        .eq('user_id', userId)
        .eq('status', 'pending')
        .lt('expires_at', new Date().toISOString());
}

/**
 * Create a challenge and push both messages.
 *
 * The password must already be verified by the caller, otherwise anyone who
 * knows a username could spam a stranger's chat with approval prompts.
 */
export async function createChallenge(user, link, req, { purpose = 'login', withCode = true } = {}) {
    await expireStaleChallenges(user.id);

    const { count } = await supabase
        .from('uwusuite_mfa_challenges')
        .select('id', { count: 'exact', head: true })
        .eq('user_id', user.id)
        .gte('created_at', isoAgo(CHALLENGE_BUDGET.windowSeconds));

    if ((count || 0) >= CHALLENGE_BUDGET.max) {
        throw { status: 429, message: 'Too many sign in attempts, please wait a few minutes' };
    }

    // One live challenge per user. A second attempt cancels the first rather
    // than sending two prompts.
    const { data: superseded } = await supabase
        .from('uwusuite_mfa_challenges')
        .select('id, message_id, code_message_id')
        .eq('user_id', user.id)
        .eq('status', 'pending');

    for (const old of superseded || []) {
        await supabase
            .from('uwusuite_mfa_challenges')
            .update({ status: 'expired', resolved_at: new Date().toISOString() })
            .eq('id', old.id);
        await clearChallengeMessages(link.telegram_id, old, COPY.promptExpired);
    }

    const matchNumber = generateMatchNumber();
    const magicToken = generateMagicToken();

    const { data: challenge, error } = await supabase
        .from('uwusuite_mfa_challenges')
        .insert({
            user_id: user.id,
            telegram_id: link.telegram_id,
            match_number: matchNumber,
            purpose,
            ip: clientIp(req),
            device: detectDevice(req),
            user_agent: String(req.headers['user-agent'] || '').slice(0, 500),
            expires_at: isoIn(CHALLENGE_TTL_SECONDS),
            magic_token_hash: hashMagicToken(magicToken)
        })
        .select('*')
        .single();

    if (error) throw { status: 500, message: 'Could not start the second step' };

    const delivery = await pushChallengeMessages(challenge, link, req, magicToken, withCode);

    return { challenge, matchNumber, delivered: delivery.promptDelivered };
}

/**
 * Two messages, not one. The prompt is a decision with Approve and This was not
 * me on it, the code message is a credential with a Sign in button and a copy
 * button on it.
 */
async function pushChallengeMessages(challenge, link, req, magicToken, withCode = true) {
    const prompt = await sendBotMessage(
        link.telegram_id,
        COPY.approvalPrompt({
            matchNumber: challenge.match_number,
            time: new Date(challenge.created_at).toUTCString(),
            device: challenge.device,
            ip: challenge.ip,
            location: coarseLocation(req)
        }),
        { inline_keyboard: COPY.approvalButtons(challenge.id) }
    );

    const patch = {};
    if (prompt.ok) patch.message_id = prompt.result.message_id;

    // The code counts against the account's issue budget, so repeated login
    // attempts are not a cheaper way to flood the chat than repeated /code.
    // An enrolment test sends only the prompt: there is no login to complete.
    const issued = withCode
        ? await issueOtp(challenge.user_id, { purpose: 'login', source: 'login' })
        : { rateLimited: true };

    if (!issued.rateLimited) {
        const codeMessage = await sendWithOptionalCopyButton(
            link.telegram_id,
            COPY.codeMessage({ code: issued.code, minutes: Math.round(OTP_TTL_SECONDS / 60) }),
            [[{ text: 'Sign in', url: magicUrl(magicToken) }]],
            { text: 'Copy the code', copy_text: { text: issued.code } }
        );
        // If the code message fails the login is not broken: the prompt and the
        // recovery code entry both still work.
        if (codeMessage.ok) patch.code_message_id = codeMessage.result.message_id;
    }

    if (Object.keys(patch).length) {
        await supabase.from('uwusuite_mfa_challenges').update(patch).eq('id', challenge.id);
        Object.assign(challenge, patch);
    }

    return { promptDelivered: prompt.ok };
}

/**
 * The portal composed both messages, so the portal cleans them up: the prompt
 * loses its buttons and the code message loses its copy button in the same step.
 */
export async function clearChallengeMessages(telegramId, challenge, outcome) {
    if (challenge.message_id) {
        await editBotMessage(telegramId, challenge.message_id, outcome, { inline_keyboard: [] });
    }
    if (challenge.code_message_id) {
        await editBotMessage(
            telegramId, challenge.code_message_id, COPY.codeSuperseded, { inline_keyboard: [] }
        );
    }
}

export async function loadChallenge(challengeId) {
    if (!/^[0-9a-f-]{36}$/i.test(String(challengeId || ''))) return null;
    const { data } = await supabase
        .from('uwusuite_mfa_challenges')
        .select('*')
        .eq('id', challengeId)
        .single();
    if (!data) return null;

    // Lazy expiry sweep. There is no scheduler on this side, so a stale row is
    // marked the moment anybody looks at it rather than left pending forever.
    if (data.status === 'pending' && new Date(data.expires_at) < new Date()) {
        await supabase
            .from('uwusuite_mfa_challenges')
            .update({ status: 'expired', resolved_at: new Date().toISOString() })
            .eq('id', data.id);
        data.status = 'expired';
    }
    return data;
}

export async function markChallenge(challengeId, status) {
    const { data } = await supabase
        .from('uwusuite_mfa_challenges')
        .update({ status, resolved_at: new Date().toISOString() })
        .eq('id', challengeId)
        .eq('status', 'pending')
        .select('*')
        .single();
    return data || null;
}

/**
 * A denial does more than deny. It kills every active session for the account,
 * because a denial means somebody else already has the password.
 */
export async function killAllSessions(userId) {
    await supabase.from('uwusuite_sessions').delete().eq('user_id', userId);
}

/* --- the session at the end of it all ------------------------------------- */

/**
 * The normal login payload, so the rest of the client code is untouched
 * whether the session came from a password alone or from a second step.
 */
export async function createSession(user) {
    const token = generateSessionToken();
    const expiresAt = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString();

    const { error: sessionErr } = await supabase
        .from('uwusuite_sessions')
        .insert({ user_id: user.id, token, expires_at: expiresAt });
    if (sessionErr) throw { status: 500, message: 'Could not create session' };

    return {
        token,
        expiresAt,
        user: serializeUser(user)
    };
}

/** Approve a challenge and hand back the login payload, from any of the paths. */
export async function completeChallenge(challenge) {
    const user = await userById(challenge.user_id);
    if (!user) throw { status: 404, message: 'Account not found' };
    const link = await linkForTelegramId(challenge.telegram_id);
    if (link) await clearChallengeMessages(link.telegram_id, challenge, COPY.promptApproved);
    return createSession(user);
}
