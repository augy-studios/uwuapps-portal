// api/auth/guest-key.js — issues short-lived signing keys for unauthenticated PWAs

import { supabase, cors } from '../_supabase.js';
import { randomBytes, randomUUID } from 'node:crypto';

const TTL_MINUTES = 10;

export default async function handler(req, res) {
    if (cors(req, res)) return;
    if (req.method !== 'GET') {
        return res.status(405).json({ ok: false, error: 'Method not allowed' });
    }

    const allowed = (process.env.ALLOWED_ORIGINS || '').split(',').map(s => s.trim()).filter(Boolean);
    const origin = req.headers['origin'] || '';
    if (allowed.length && !allowed.includes(origin)) {
        return res.status(403).json({ ok: false, error: 'Origin not allowed' });
    }

    const appId = req.query.app || 'unknown';
    const signingKey = randomBytes(32).toString('hex');
    const sessionToken = randomUUID();
    const expiresAt = new Date(Date.now() + TTL_MINUTES * 60 * 1000).toISOString();

    const { data, error } = await supabase
        .from('uwu_signing_keys')
        .insert({
            session_token: sessionToken,
            signing_key: signingKey,
            is_guest: true,
            app_id: appId,
            expires_at: expiresAt
        })
        .select('id')
        .single();

    if (error) {
        console.error('[guest-key]', error);
        return res.status(500).json({ ok: false, error: 'Could not issue key' });
    }

    return res.status(200).json({
        ok: true,
        key_id: data.id,
        signing_key: signingKey
    });
}
