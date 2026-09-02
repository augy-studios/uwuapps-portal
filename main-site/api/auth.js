// api/auth.js
// POST /api/auth, body: { action, ...params }

import {
    supabase,
    hashPassword,
    verifyPassword,
    resolveSession,
    serializeUser,
    ok,
    err,
    cors
} from './_supabase.js';
import { MAX_POLLS_PER_CHALLENGE } from '../lib/uwu-telegram.js';
import {
    completeChallenge,
    consumeRecoveryCode,
    createChallenge,
    createSession,
    linkForUser,
    loadChallenge,
    markChallenge,
    verifyOtp
} from './_mfa.js';

export default async function handler(req, res) {
    if (cors(req, res)) return;
    if (req.method !== 'POST') return res.status(405).json({
        ok: false,
        error: 'Method not allowed'
    });

    const {
        action
    } = req.body || {};

    try {
        switch (action) {

            // Register
            case 'register': {
                const {
                    username,
                    displayName,
                    email,
                    password
                } = req.body;

                if (!username || !email || !password) {
                    throw {
                        status: 400,
                        message: 'username, email, and password are required'
                    };
                }
                if (password.length < 8) {
                    throw {
                        status: 400,
                        message: 'Password must be at least 8 characters'
                    };
                }
                if (!/^[a-z0-9_-]{3,30}$/i.test(username)) {
                    throw {
                        status: 400,
                        message: 'Username must be 3–30 chars, letters/numbers/_ only'
                    };
                }

                // Check for duplicate email or username
                const {
                    data: existing
                } = await supabase
                    .from('uwusuite_users')
                    .select('id')
                    .or(`email.eq.${email},username.eq.${username}`)
                    .limit(1);

                if (existing?.length) {
                    throw {
                        status: 409,
                        message: 'Email or username already in use'
                    };
                }

                // Check pre-approval list
                const {
                    data: preapproved
                } = await supabase
                    .from('uwusuite_users_preapproved')
                    .select('*')
                    .eq('email', email.toLowerCase())
                    .is('activated_at', null)
                    .single();

                const isPreapproved = !!preapproved;
                const isAdmin = isPreapproved && preapproved.preapproved_role === 'admin';
                const isEditor = isPreapproved && ['editor', 'admin'].includes(preapproved.preapproved_role);

                const passwordHash = await hashPassword(password);

                const {
                    data: newUser,
                    error: insertErr
                } = await supabase
                    .from('uwusuite_users')
                    .insert({
                        username: username.toLowerCase(),
                        display_name: displayName || username,
                        email: email.toLowerCase(),
                        password_hash: passwordHash,
                        is_approved: isPreapproved,
                        is_admin: isAdmin,
                        is_editor: isEditor
                    })
                    .select()
                    .single();

                if (insertErr) throw {
                    status: 500,
                    message: insertErr.message
                };

                // Mark pre-approval as activated
                if (isPreapproved) {
                    await supabase
                        .from('uwusuite_users_preapproved')
                        .update({
                            activated_at: new Date().toISOString(),
                            user_id: newUser.id
                        })
                        .eq('id', preapproved.id);
                }

                return ok(res, {
                    message: isPreapproved ?
                        'Account created and pre-approved, you can log in now!' :
                        'Account created, awaiting admin approval',
                    preapproved: isPreapproved
                }, 201);
            }

            // Login
            case 'login': {
                const {
                    username,
                    password
                } = req.body;
                if (!username || !password) throw {
                    status: 400,
                    message: 'Username and password required'
                };

                const {
                    data: user
                } = await supabase
                    .from('uwusuite_users')
                    .select('*')
                    .eq('username', username.toLowerCase())
                    .single();

                if (!user) throw {
                    status: 401,
                    message: 'Invalid email or password'
                };

                const valid = await verifyPassword(password, user.password_hash);
                if (!valid) throw {
                    status: 401,
                    message: 'Invalid email or password'
                };

                // The password check runs before any challenge is sent, so
                // knowing a username cannot be turned into a way to spam a
                // stranger's chat with approval prompts.
                const link = await linkForUser(user.id);

                // No link, or the second factor off, and login proceeds exactly
                // as it did before. Nothing changes for accounts that have not
                // opted in, and there is no extra round trip.
                if (!link || !link.mfa_enabled) {
                    return ok(res, await createSession(user));
                }

                // Second factor on, so no session yet.
                const { challenge, matchNumber, delivered } = await createChallenge(user, link, req);

                return ok(res, {
                    mfa_required: true,
                    challenge_id: challenge.id,
                    match_number: matchNumber,
                    expires_at: challenge.expires_at,
                    // The prompt could not be delivered, so offer the recovery
                    // code entry immediately rather than a spinner that never ends.
                    prompt_delivered: delivered
                });
            }

            // Poll for the outcome of a challenge, mid login.
            // The challenge id is the only thing the polling client presents,
            // which is why it is unguessable.
            case 'mfa_status': {
                const challenge = await requirePendingOrResolved(req.body.challenge_id);

                if (challenge.poll_count >= MAX_POLLS_PER_CHALLENGE) {
                    throw { status: 429, message: 'Too many checks, please start the sign in again' };
                }
                await supabase
                    .from('uwusuite_mfa_challenges')
                    .update({
                        poll_count: challenge.poll_count + 1,
                        last_poll_at: new Date().toISOString()
                    })
                    .eq('id', challenge.id);

                if (challenge.status === 'pending') return ok(res, { status: 'pending' });
                if (challenge.status !== 'approved') {
                    return ok(res, {
                        status: challenge.status,
                        message: challenge.status === 'denied'
                            ? 'That sign in was stopped. Change your password now.'
                            : 'That sign in request expired. Please try again.'
                    });
                }

                return ok(res, { status: 'approved', ...(await completeChallenge(challenge)) });
            }

            // A code typed into the waiting page. The code binds to the user,
            // not to the challenge, so it survives a page reload, and the check
            // is that the code belongs to the same user the challenge does.
            case 'mfa_verify_code': {
                const challenge = await requirePendingOrResolved(req.body.challenge_id);
                if (challenge.status !== 'pending') {
                    throw { status: 409, message: 'That sign in request is no longer waiting' };
                }

                const check = await verifyOtp(challenge.user_id, 'login', req.body.code);
                if (!check.ok) throw { status: 400, message: check.reason };

                const resolved = await markChallenge(challenge.id, 'approved');
                if (!resolved) throw { status: 409, message: 'That sign in request is no longer waiting' };

                return ok(res, { status: 'approved', ...(await completeChallenge(resolved)) });
            }

            // The path that works with the chat unreachable and the bot stopped.
            case 'mfa_recover': {
                const challenge = await requirePendingOrResolved(req.body.challenge_id);
                if (challenge.status !== 'pending') {
                    throw { status: 409, message: 'That sign in request is no longer waiting' };
                }

                const consumed = await consumeRecoveryCode(challenge.user_id, req.body.recovery_code);
                if (!consumed) throw { status: 400, message: 'That recovery code is not right' };

                const resolved = await markChallenge(challenge.id, 'approved');
                if (!resolved) throw { status: 409, message: 'That sign in request is no longer waiting' };

                return ok(res, { status: 'approved', ...(await completeChallenge(resolved)) });
            }

            // Me, validate token + return user
            case 'me': {
                const user = await resolveSession(req);
                if (!user) throw {
                    status: 401,
                    message: 'Not authenticated or session expired'
                };

                return ok(res, {
                    user: serializeUser(user)
                });
            }

            // Logout
            case 'logout': {
                const auth = req.headers['authorization'] || '';
                const logoutToken = auth.replace(/^Bearer\s+/i, '').trim();
                if (logoutToken) {
                    await supabase.from('uwusuite_sessions').delete().eq('token', logoutToken);
                }
                return ok(res, {
                    message: 'Logged out'
                });
            }

            default:
                throw {
                    status: 400, message: `Unknown action: ${action}`
                };
        }
    } catch (e) {
        return err(res, e);
    }
}

/**
 * Loads a challenge for one of the three mid login actions.
 *
 * The reason for a refusal is never leaked beyond this, since the page is
 * reachable by anyone holding the password.
 */
async function requirePendingOrResolved(challengeId) {
    const challenge = await loadChallenge(challengeId);
    if (!challenge) throw { status: 404, message: 'That sign in request is no longer valid' };
    return challenge;
}