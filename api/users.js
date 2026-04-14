// api/users.js

import {
    supabase,
    requireAdmin,
    serializeUser,
    ok,
    err,
    cors
} from './_supabase.js';

export default async function handler(req, res) {
    if (cors(req, res)) return;

    // Route: /api/users/preapprove
    if (req.url?.includes('/preapprove') || req.query?.action === 'preapprove') {
        return handlePreapprove(req, res);
    }

    try {
        switch (req.method) {

            // ── GET — all users + pending preapprovals ───────
            case 'GET': {
                await requireAdmin(req);

                const [{
                    data: users
                }, {
                    data: preapproved
                }] = await Promise.all([
                    supabase
                    .from('uwusuite_users')
                    .select('id, username, display_name, email, is_admin, is_editor, is_approved, avatar_url, created_at')
                    .order('created_at'),
                    supabase
                    .from('uwusuite_users_preapproved')
                    .select('id, email, preapproved_role, preapproved_at, activated_at, preapproved_by')
                    .order('preapproved_at')
                ]);

                return ok(res, {
                    users: (users || []).map(serializeUser),
                    preapproved: preapproved || []
                });
            }

            // ── PUT — update user flags ──────────────────────
            case 'PUT': {
                const adminUser = await requireAdmin(req);
                const {
                    id
                } = req.query;
                if (!id) throw {
                    status: 400,
                    message: 'id required'
                };
                if (id === adminUser.id) throw {
                    status: 400,
                    message: 'Cannot modify your own account here'
                };

                const {
                    isAdmin,
                    isEditor,
                    isApproved,
                    displayName
                } = req.body || {};
                const patch = {};
                if (isAdmin !== undefined) patch.is_admin = !!isAdmin;
                if (isEditor !== undefined) patch.is_editor = !!isEditor;
                if (isApproved !== undefined) patch.is_approved = !!isApproved;
                if (displayName !== undefined) patch.display_name = displayName;

                const {
                    data,
                    error
                } = await supabase
                    .from('uwusuite_users')
                    .update(patch)
                    .eq('id', id)
                    .select()
                    .single();

                if (error) throw error;
                return ok(res, {
                    user: serializeUser(data)
                });
            }

            // ── DELETE — remove user ─────────────────────────
            case 'DELETE': {
                const adminUser = await requireAdmin(req);
                const {
                    id
                } = req.query;
                if (!id) throw {
                    status: 400,
                    message: 'id required'
                };
                if (id === adminUser.id) throw {
                    status: 400,
                    message: 'Cannot delete your own account'
                };

                // Sessions and apps (created_by) cascade on delete via FK
                const {
                    error
                } = await supabase.from('uwusuite_users').delete().eq('id', id);
                if (error) throw error;
                return ok(res, {
                    message: 'User deleted'
                });
            }

            default:
                return res.status(405).json({
                    ok: false,
                    error: 'Method not allowed'
                });
        }
    } catch (e) {
        return err(res, e);
    }
}

/* ── Preapproval sub-handler ─────────────────────────────── */
async function handlePreapprove(req, res) {
    try {
        const adminUser = await requireAdmin(req);

        // POST — add preapproval
        if (req.method === 'POST') {
            const {
                email,
                role = 'editor'
            } = req.body || {};
            if (!email) throw {
                status: 400,
                message: 'email required'
            };
            if (!['editor', 'admin'].includes(role)) throw {
                status: 400,
                message: 'role must be editor or admin'
            };

            // Check if user already exists — if so, approve them directly
            const {
                data: existingUser
            } = await supabase
                .from('uwusuite_users')
                .select('id, is_approved, is_editor, is_admin')
                .eq('email', email.toLowerCase())
                .single();

            if (existingUser) {
                // User already registered — approve them now
                const patch = {
                    is_approved: true,
                    is_editor: role === 'editor' || role === 'admin',
                    is_admin: role === 'admin'
                };
                await supabase.from('uwusuite_users').update(patch).eq('id', existingUser.id);
                return ok(res, {
                    message: 'Existing user approved and role updated',
                    directApproval: true
                });
            }

            // User doesn't exist yet — add to preapproval list
            const {
                data,
                error
            } = await supabase
                .from('uwusuite_users_preapproved')
                .upsert({
                    email: email.toLowerCase(),
                    preapproved_role: role,
                    preapproved_by: adminUser.id,
                    preapproved_at: new Date().toISOString(),
                    activated_at: null
                }, {
                    onConflict: 'email'
                })
                .select()
                .single();

            if (error) throw error;
            return ok(res, {
                preapproval: data
            }, 201);
        }

        // DELETE — remove preapproval
        if (req.method === 'DELETE') {
            const {
                id
            } = req.query;
            if (!id) throw {
                status: 400,
                message: 'id required'
            };
            const {
                error
            } = await supabase
                .from('uwusuite_users_preapproved')
                .delete()
                .eq('id', id);
            if (error) throw error;
            return ok(res, {
                message: 'Preapproval removed'
            });
        }

        return res.status(405).json({
            ok: false,
            error: 'Method not allowed'
        });
    } catch (e) {
        return err(res, e);
    }
}