// api/apps.js

const ALLOWED_TAGS = ['tools', 'games', 'bots', 'singapore'];

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

export default async function handler(req, res) {
    if (cors(req, res)) return;

    try {
        switch (req.method) {

            // ── GET — list apps ──────────────────────────────
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

            // ── POST — create app ────────────────────────────
            case 'POST': {
                const user = await requireContributor(req);
                const {
                    title,
                    url,
                    description,
                    tags,
                    galleryUrls,
                    thumbnailIndex,
                    published,
                    sortOrder,
                    publishedDate
                } = req.body || {};

                if (!title || !url) throw {
                    status: 400,
                    message: 'title and url are required'
                };

                const gallery = Array.isArray(galleryUrls) ? galleryUrls : [];
                const thumbIdx = Math.max(0, Math.min(parseInt(thumbnailIndex) || 0, gallery.length - 1));

                const tagList = Array.isArray(tags) ? tags : [];
                const invalidTags = tagList.filter(t => !ALLOWED_TAGS.includes(t));
                if (invalidTags.length > 0) return err(res, 400, `Invalid tags: ${invalidTags.join(', ')}`);

                const {
                    data,
                    error
                } = await supabase
                    .from('uwusuite_apps')
                    .insert({
                        title,
                        url,
                        description: description || null,
                        tags: tagList,
                        gallery_urls: gallery,
                        thumbnail_url: gallery[thumbIdx] || null,
                        thumbnail_index: thumbIdx,
                        published: !!published,
                        sort_order: typeof sortOrder === 'number' ? sortOrder : 0,
                        published_date: publishedDate || null,
                        created_by: user.id,
                        updated_by: user.id
                    })
                    .select()
                    .single();

                if (error) throw error;
                return ok(res, {
                    app: data
                }, 201);
            }

            // ── PUT — update app ─────────────────────────────
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
                    title,
                    url,
                    description,
                    tags,
                    galleryUrls,
                    thumbnailIndex,
                    published,
                    sortOrder,
                    publishedDate
                } = req.body || {};

                const patch = {
                    updated_by: user.id
                };
                if (title !== undefined) patch.title = title;
                if (url !== undefined) patch.url = url;
                if (description !== undefined) patch.description = description || null;
                if (tags !== undefined) {
                    const tagList = Array.isArray(tags) ? tags : [];
                    const invalid = tagList.filter(t => !ALLOWED_TAGS.includes(t));
                    if (invalid.length > 0) return err(res, 400, `Invalid tags: ${invalid.join(', ')}`);
                    patch.tags = tagList;
                }
                if (published !== undefined) patch.published = !!published;
                if (sortOrder !== undefined) patch.sort_order = sortOrder;
                if (publishedDate !== undefined) patch.published_date = publishedDate || null;

                if (galleryUrls !== undefined) {
                    const gallery = Array.isArray(galleryUrls) ? galleryUrls : [];
                    const thumbIdx = Math.max(0, Math.min(parseInt(thumbnailIndex) || 0, gallery.length - 1));
                    patch.gallery_urls = gallery;
                    patch.thumbnail_url = gallery[thumbIdx] || null;
                    patch.thumbnail_index = thumbIdx;
                }

                const {
                    data,
                    error
                } = await supabase
                    .from('uwusuite_apps')
                    .update(patch)
                    .eq('id', id)
                    .select()
                    .single();

                if (error) throw error;
                return ok(res, {
                    app: data
                });
            }

            // ── DELETE — remove app ──────────────────────────
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