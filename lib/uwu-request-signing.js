// /lib/uwu-request-signing.js — client-side request signing for UwU Suite PWAs

(function () {
    'use strict';

    function bufToHex(buf) {
        return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
    }

    window.storeSigningKey = function (signingKey, keyId) {
        sessionStorage.setItem('uwu_signing_key', signingKey);
        sessionStorage.setItem('uwu_key_id', keyId);
    };

    window.clearSigningKey = function () {
        sessionStorage.removeItem('uwu_signing_key');
        sessionStorage.removeItem('uwu_key_id');
    };

    window.initGuestKey = async function (appId) {
        if (sessionStorage.getItem('uwu_signing_key')) return;
        try {
            const res = await fetch(`/api/auth/guest-key?app=${encodeURIComponent(appId)}`);
            const data = await res.json();
            if (data.ok) {
                window.storeSigningKey(data.signing_key, data.key_id);
            }
        } catch (_) {}
    };

    window.signedFetch = async function (url, options = {}) {
        const signingKey = sessionStorage.getItem('uwu_signing_key');
        const keyId = sessionStorage.getItem('uwu_key_id');

        if (!signingKey || !keyId) {
            return fetch(url, options);
        }

        const method = (options.method || 'GET').toUpperCase();
        const bodyStr = options.body || '';
        const ts = Math.floor(Date.now() / 1000).toString();
        const urlObj = new URL(url, location.origin);
        const path = urlObj.pathname + urlObj.search;

        const encoder = new TextEncoder();
        const keyData = encoder.encode(signingKey);
        const cryptoKey = await crypto.subtle.importKey(
            'raw', keyData, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
        );

        let bodyHash = 'empty';
        if (bodyStr) {
            const bodyBytes = encoder.encode(typeof bodyStr === 'string' ? bodyStr : JSON.stringify(bodyStr));
            bodyHash = bufToHex(await crypto.subtle.sign('HMAC', cryptoKey, bodyBytes));
        }

        const message = `${ts}:${method}:${path}:${bodyHash}`;
        const token = bufToHex(await crypto.subtle.sign('HMAC', cryptoKey, encoder.encode(message)));

        const headers = new Headers(options.headers || {});
        headers.set('X-Request-Token', token);
        headers.set('X-Request-TS', ts);
        headers.set('X-Key-ID', keyId);

        return fetch(url, { ...options, headers });
    };
})();
