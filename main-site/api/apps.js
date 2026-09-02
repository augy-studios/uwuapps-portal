// api/apps.js

import {
    supabase,
    resolveSession,
    requireContributor,
    requireAdmin,
    detectDevice,
    ok,
    err,
    cors
} from './_supabase.js';
// The write rules live in one module, shared with the app_ actions in
// /api/telegram, so the two ways into this table cannot drift apart.
import {
    buildCreate,
    buildPatch
} from '../lib/uwu-apps.js';

export default async function handler(req, res) {
    if (cors(req, res)) return;

    try {
        switch (req.method) {

            // GET, list apps
            case 'GET': {
                const user = await resolveSession(req);
                const canSeeAll = user?.is_approved && (user?.is_editor || user?.is_admin);

                let query = supabase
                    .from('uwusuite_apps')
                    .select(`
            id, title, description, url, tld, tags,
            thumbnail_url, gallery_urls, thumbnail_index,
            published, sort_order, access_count,
            created_by, created_at, updated_at, published_date
          `);

                if (!canSeeAll) query = query.eq('published', true);
                query = query.order('sort_order').order('created_at', {
                    ascending: false
                });

                const {
                    data,
                    error
                } = await query;
                if (error) throw error;

                return ok(res, {
                    apps: data || []
                });
            }

            // POST, create app
            case 'POST': {
                const user = await requireContributor(req);

                const {
                    data,
                    error
                } = await supabase
                    .from('uwusuite_apps')
                    .insert(buildCreate(req.body, user.id))
                    .select()
                    .single();

                if (error) throw error;
                return ok(res, {
                    app: data
                }, 201);
            }

            // PUT, update app
            case 'PUT': {
                const user = await requireContributor(req);
                const {
                    id
                } = req.query;
                if (!id) throw {
                    status: 400,
                    message: 'id query param required'
                };

                // Editors may only update their own apps
                if (!user.is_admin) {
                    const {
                        data: existing
                    } = await supabase
                        .from('uwusuite_apps')
                        .select('created_by')
                        .eq('id', id)
                        .single();
                    if (!existing) throw {
                        status: 404,
                        message: 'App not found'
                    };
                    if (existing.created_by !== user.id) {
                        throw {
                            status: 403,
                            message: 'Editors can only edit their own apps'
                        };
                    }
                }

                const {
                    data,
                    error
                } = await supabase
                    .from('uwusuite_apps')
                    .update(buildPatch(req.body, user.id))
                    .eq('id', id)
                    .select()
                    .single();

                if (error) throw error;
                return ok(res, {
                    app: data
                });
            }

            // DELETE, remove app
            case 'DELETE': {
                await requireAdmin(req);
                const {
                    id
                } = req.query;
                if (!id) throw {
                    status: 400,
                    message: 'id query param required'
                };

                // Log the deletion before it cascades away
                const {
                    data: app
                } = await supabase
                    .from('uwusuite_apps').select('title, updated_by').eq('id', id).single();

                if (app) {
                    await supabase.from('uwusuite_app_history').insert({
                        app_id: id,
                        user_id: app.updated_by,
                        event_type: 'deleted',
                        description: `App "${app.title}" was deleted`
                    });
                }

                const {
                    error
                } = await supabase.from('uwusuite_apps').delete().eq('id', id);
                if (error) throw error;
                return ok(res, {
                    message: 'App deleted'
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