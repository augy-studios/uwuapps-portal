// api/auth.js
// POST /api/auth  — body: { action, ...params }

import {
    supabase,
    hashPassword,
    verifyPassword,
    generateSessionToken,
    resolveSession,
    serializeUser,
    ok,
    err,
    cors
} from './_supabase.js';

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

            // ── Register ──────────────────────────────────────
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
                        'Account created and pre-approved — you can log in now!' :
                        'Account created — awaiting admin approval',
                    preapproved: isPreapproved
                }, 201);
            }

            // ── Login ─────────────────────────────────────────
            case 'login': {
                const {
                    email,
                    password
                } = req.body;
                if (!email || !password) throw {
                    status: 400,
                    message: 'Email and password required'
                };

                const {
                    data: user
                } = await supabase
                    .from('uwusuite_users')
                    .select('*')
                    .eq('email', email.toLowerCase())
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

                // Create DB session token
                const token = generateSessionToken();
                const expiresAt = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(); // 30 days

                const {
                    error: sessionErr
                } = await supabase
                    .from('uwusuite_sessions')
                    .insert({
                        user_id: user.id,
                        token,
                        expires_at: expiresAt
                    });

                if (sessionErr) throw {
                    status: 500,
                    message: 'Could not create session'
                };

                return ok(res, {
                    token,
                    expiresAt,
                    user: serializeUser(user)
                });
            }

            // ── Me — validate token + return user ─────────────
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

            // ── Logout ────────────────────────────────────────
            case 'logout': {
                const auth = req.headers['authorization'] || '';
                const token = auth.replace(/^Bearer\s+/i, '').trim();
                if (token) {
                    await supabase.from('uwusuite_sessions').delete().eq('token', token);
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