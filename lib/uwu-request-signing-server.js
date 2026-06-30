// /lib/uwu-request-signing-server.js — server-side request verification for UwU Suite

import { createHmac, timingSafeEqual } from 'node:crypto';

export async function verifySignedRequest(req, supabase) {
    const token = req.headers['x-request-token'];
    const ts = req.headers['x-request-ts'];
    let keyId = req.headers['x-key-id'];

    if (!token || !ts) {
        return { valid: false, reason: 'Missing signing headers' };
    }

    const now = Math.floor(Date.now() / 1000);
    if (Math.abs(now - parseInt(ts, 10)) > 30) {
        return { valid: false, reason: 'Request timestamp out of range' };
    }

    // Look up signing key by key id, or fall back to session token
    let keyRow;
    if (keyId) {
        const { data } = await supabase
            .from('uwu_signing_keys')
            .select('signing_key, session_token, expires_at')
            .eq('id', keyId)
            .single();
        keyRow = data;
    } else {
        const auth = req.headers['authorization'] || '';
        const sessionToken = auth.replace(/^Bearer\s+/i, '').trim();
        if (!sessionToken) return { valid: false, reason: 'No key ID or session token' };
        const { data } = await supabase
            .from('uwu_signing_keys')
            .select('id, signing_key, session_token, expires_at')
            .eq('session_token', sessionToken)
            .single();
        keyRow = data;
        if (keyRow) keyId = keyRow.id;
    }

    if (!keyRow) {
        return { valid: false, reason: 'Unknown signing key' };
    }
    if (new Date(keyRow.expires_at) < new Date()) {
        return { valid: false, reason: 'Signing key expired' };
    }

    // Recompute HMAC
    const method = req.method.toUpperCase();
    const path = req.url;
    const bodyStr = req.body ? (typeof req.body === 'string' ? req.body : JSON.stringify(req.body)) : '';

    let bodyHash = 'empty';
    if (bodyStr) {
        bodyHash = createHmac('sha256', keyRow.signing_key).update(bodyStr).digest('hex');
    }

    const message = `${ts}:${method}:${path}:${bodyHash}`;
    const expected = createHmac('sha256', keyRow.signing_key).update(message).digest('hex');

    // Constant-time compare
    try {
        const tokenBuf = Buffer.from(token, 'hex');
        const expectedBuf = Buffer.from(expected, 'hex');
        if (tokenBuf.length !== expectedBuf.length || !timingSafeEqual(tokenBuf, expectedBuf)) {
            return { valid: false, reason: 'Invalid signature' };
        }
    } catch {
        return { valid: false, reason: 'Invalid token format' };
    }

    // Replay check
    const { data: used } = await supabase
        .from('uwu_used_request_tokens')
        .select('token')
        .eq('token', token)
        .single();

    if (used) {
        return { valid: false, reason: 'Token already used' };
    }

    await supabase.from('uwu_used_request_tokens').insert({
        token,
        session_token: keyRow.session_token,
        used_at: new Date().toISOString()
    });

    return { valid: true, reason: 'OK' };
}
