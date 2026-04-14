// api/_supabase.js

import {
    createClient
} from '@supabase/supabase-js';
import {
    createHash,
    randomBytes
} from 'crypto';
import {
    hash as bcryptHash,
    compare as bcryptCompare
} from 'bcryptjs';

const supabaseUrl = process.env.SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!supabaseUrl || !supabaseKey) {
    throw new Error('Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY');
}

export const supabase = createClient(supabaseUrl, supabaseKey, {
    auth: {
        persistSession: false
    }
});

/* ── Password hashing (bcrypt via bcryptjs) ─────────────── */
export const hashPassword = (plain) => bcryptHash(plain, 12);
export const verifyPassword = (plain, hashed) => bcryptCompare(plain, hashed);

/* ── Session token generation ────────────────────────────── */
export function generateSessionToken() {
    return randomBytes(48).toString('hex'); // 96-char hex string
}

/* ── Resolve session token → user ────────────────────────── */
export async function resolveSession(req) {
    const auth = req.headers['authorization'] || '';
    const token = auth.replace(/^Bearer\s+/i, '').trim();
    if (!token) return null;

    const {
        data: session
    } = await supabase
        .from('uwusuite_sessions')
        .select('user_id, expires_at')
        .eq('token', token)
        .single();

    if (!session) return null;
    if (new Date(session.expires_at) < new Date()) {
        // Clean up expired session
        await supabase.from('uwusuite_sessions').delete().eq('token', token);
        return null;
    }

    const {
        data: user
    } = await supabase
        .from('uwusuite_users')
        .select('id, username, display_name, email, is_admin, is_editor, is_approved, avatar_url')
        .eq('id', session.user_id)
        .single();

    return user || null;
}

/* ── Auth guards ─────────────────────────────────────────── */
export async function requireAuth(req) {
    const user = await resolveSession(req);
    if (!user) throw {
        status: 401,
        message: 'Not authenticated'
    };
    return user;
}

export async function requireContributor(req) {
    const user = await requireAuth(req);
    if (!user.is_approved || (!user.is_editor && !user.is_admin)) {
        throw {
            status: 403,
            message: 'Insufficient permissions'
        };
    }
    return user;
}

export async function requireAdmin(req) {
    const user = await requireAuth(req);
    if (!user.is_approved || !user.is_admin) {
        throw {
            status: 403,
            message: 'Admin only'
        };
    }
    return user;
}

/* ── Device type detection ───────────────────────────────── */
export function detectDevice(req) {
    const ua = (req.headers['user-agent'] || '').toLowerCase();
    if (/tablet|ipad/.test(ua)) return 'Tablet';
    if (/mobile|android|iphone|ipod/.test(ua)) return 'Mobile';
    if (ua) return 'Desktop';
    return 'Others';
}

/* ── Standard JSON response helpers ─────────────────────── */
export function ok(res, data, status = 200) {
    res.status(status).json({
        ok: true,
        ...data
    });
}

export function err(res, e) {
    const status = typeof e?.status === 'number'?e.status : 500;
    const message = typeof e?.message === 'string'?e.message : 'Internal server error';
    console.error('[uwusuite api error]', status, message, e);
    res.status(status).json({
        ok: false,
        error: message
    });
}

/* ── CORS — call at the top of every handler ─────────────── */
export function cors(req, res) {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type,Authorization');
    if (req.method === 'OPTIONS') {
        res.status(204).end();
        return true;
    }
    return false;
}

/* ── Serialise user for API responses ────────────────────── */
export function serializeUser(user) {
    return {
        id: user.id,
        username: user.username,
        displayName: user.display_name,
        email: user.email,
        isAdmin: user.is_admin,
        isEditor: user.is_editor,
        isApproved: user.is_approved,
        avatarUrl: user.avatar_url || null
    };
}