// api/upload.js

import {
    supabase,
    requireContributor,
    ok,
    err,
    cors
} from './_supabase.js';

const BUCKET = 'uwusuite-media';

export default async function handler(req, res) {
    if (cors(req, res)) return;
    if (req.method !== 'POST') return res.status(405).json({
        ok: false,
        error: 'Method not allowed'
    });

    try {
        await requireContributor(req);

        const {
            appId,
            fileName
        } = req.body || {};
        if (!appId || !fileName) throw {
            status: 400,
            message: 'appId and fileName required'
        };

        const safeName = fileName.replace(/[^a-z0-9._-]/gi, '_').replace(/\.+$/, '') + '.webp';
        const path = `apps/${appId}/${Date.now()}-${safeName}`;

        const {
            data,
            error
        } = await supabase.storage
            .from(BUCKET)
            .createSignedUploadUrl(path);

        if (error) throw error;

        const {
            data: pub
        } = supabase.storage.from(BUCKET).getPublicUrl(path);

        return ok(res, {
            signedUrl: data.signedUrl,
            path,
            publicUrl: pub.publicUrl
        });
    } catch (e) {
        return err(res, e);
    }
}