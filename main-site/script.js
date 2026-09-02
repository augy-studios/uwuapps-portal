'use strict';

/* SESSION, localStorage key: uwusuite_session
   Stores: { token, expiresAt, user } */
const SESSION_KEY = 'uwusuite_session';

const session = {
    save(data) {
        localStorage.setItem(SESSION_KEY, JSON.stringify(data));
    },
    load() {
        try {
            return JSON.parse(localStorage.getItem(SESSION_KEY));
        } catch {
            return null;
        }
    },
    clear() {
        localStorage.removeItem(SESSION_KEY);
    },
    token() {
        return this.load()?.token || null;
    },
    isExpired() {
        const s = this.load();
        if (!s?.expiresAt) return true;
        return new Date(s.expiresAt) < new Date();
    }
};

/* API FETCH, always sends Bearer token if present */
async function apiFetch(path, options = {}) {
    const token = session.token();
    const headers = {
        'Content-Type': 'application/json'
    };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const res = await fetch(path, {
        method: options.method || 'GET',
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined
    });

    const data = await res.json();

    if (res.status === 401) {
        session.clear();
        currentUser = null;
        renderAuthUi();
        throw {
            status: 401,
            message: data.error || 'Session expired, please log in again'
        };
    }

    if (!data.ok) throw {
        status: res.status,
        message: data.error || 'Request failed'
    };
    return data;
}

/* STATE */
let currentUser = null;
let allApps = [];
let filteredApps = [];
let activeTagFilter = '';
let activeSort = 'sort_order';
let editingAppId = null;
let pendingDeleteId = null;
let galleryFiles = [];
let galleryUrls = [];
let selectedThumbIndex = 0;

/* DOM HELPERS */
const $ = id => document.getElementById(id);
const toast = $('toast');
let toastTimer;

function showToast(msg, duration = 2800) {
    toast.textContent = msg;
    toast.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.add('hidden'), duration);
}

function openModal(id) {
    $(id).classList.remove('hidden');
}

function closeModal(id) {
    $(id).classList.add('hidden');
}

function escHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function extractTld(url) {
    try {
        const parts = new URL(url).hostname.split('.');
        return parts.length >= 2 ? parts.slice(-2).join('.') : url;
    } catch {
        return url;
    }
}

function tagClass(tag) {
    return {
        tools: 'pill-tools',
        games: 'pill-games',
        bots: 'pill-bots',
        singapore: 'pill-singapore'
    } [tag] || '';
}

function formatDate(iso) {
    return new Date(iso).toLocaleDateString('en-SG', {
        day: 'numeric',
        month: 'short',
        year: 'numeric'
    });
}

function canContribute() {
    return currentUser?.isApproved && (currentUser?.isEditor || currentUser?.isAdmin);
}

function isAdmin() {
    return currentUser?.isApproved && currentUser?.isAdmin;
}

/* Theme lives in js/theme.js, wired from the module block in index.html */

/* AUTH UI */
function renderAuthUi() {
    const loggedIn = !!currentUser;
    $('loginBtn').classList.toggle('hidden', loggedIn);
    $('userMenu').classList.toggle('hidden', !loggedIn);

    if (loggedIn) {
        const name = currentUser.displayName || currentUser.username;
        $('userAvatar').textContent = name.charAt(0).toUpperCase();
        $('dropdownName').textContent = name;

        // Role badge
        let roleTxt = 'pending';
        if (currentUser.isAdmin) roleTxt = 'admin';
        else if (currentUser.isEditor) roleTxt = 'editor';
        else if (currentUser.isApproved) roleTxt = 'viewer';
        $('dropdownRole').textContent = roleTxt;

        $('addAppBtn').classList.toggle('hidden', !canContribute());
        $('adminPanelBtn').classList.toggle('hidden', !isAdmin());
    }
}

/* BOOT */
async function boot() {
    $('footerYear').textContent = new Date().getFullYear();

    const stored = session.load();
    if (stored?.token && !session.isExpired()) {
        try {
            const res = await apiFetch('/api/auth', {
                method: 'POST',
                body: {
                    action: 'me'
                }
            });
            currentUser = res.user;
        } catch (_) {
            session.clear();
            currentUser = null;
        }
    } else if (stored) {
        session.clear();
    }

    renderAuthUi();
    await loadApps();
}
boot();

/* AUTH MODAL */
let authMode = 'login';

$('loginBtn').addEventListener('click', () => {
    setAuthMode('login');
    openModal('authModal');
});

function setAuthMode(mode) {
    authMode = mode;
    $('authModalTitle').textContent = mode === 'login' ? 'Log in' : 'Register';
    $('authSubmit').textContent = mode === 'login' ? 'Log in' : 'Create account';
    $('registerFields').classList.toggle('hidden', mode !== 'register');
    $('authEmailLabel').classList.toggle('hidden', mode !== 'register');
    $('authLoginUsernameLabel').classList.toggle('hidden', mode !== 'login');
    $('authPassword').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
    $('authError').classList.add('hidden');
    document.querySelectorAll('.auth-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === mode));

    $('authFootnote').innerHTML = mode === 'login' ?
        'New here? <button class="link-btn" data-tab="register">Create an account</button><br/><small>New accounts require admin approval before you can contribute.</small>' :
        'Already have an account? <button class="link-btn" data-tab="login">Log in</button>';
    $('authFootnote').querySelectorAll('[data-tab]').forEach(b =>
        b.addEventListener('click', () => setAuthMode(b.dataset.tab))
    );
}

document.querySelectorAll('.auth-tab').forEach(btn =>
    btn.addEventListener('click', () => setAuthMode(btn.dataset.tab))
);

$('authForm').addEventListener('submit', async e => {
    e.preventDefault();
    const errEl = $('authError');
    const submitBtn = $('authSubmit');
    errEl.classList.add('hidden');
    submitBtn.textContent = '…';
    submitBtn.disabled = true;

    const email = $('authEmail').value.trim();
    const loginUsername = $('authLoginUsername').value.trim();
    const password = $('authPassword').value;
    const username = $('authUsername')?.value.trim();
    const displayName = $('authName')?.value.trim();

    try {
        if (authMode === 'register') {
            const res = await apiFetch('/api/auth', {
                method: 'POST',
                body: {
                    action: 'register',
                    username,
                    displayName,
                    email,
                    password
                }
            });
            showToast(res.message || (res.preapproved ?
                'Account created! You can log in now' :
                'Account created! Awaiting admin approval'), 5000);
            closeModal('authModal');
        } else {
            const res = await apiFetch('/api/auth', {
                method: 'POST',
                body: {
                    action: 'login',
                    username: loginUsername,
                    password
                }
            });

            // The account has the second factor on, so there is no session yet.
            // The login is held until it is approved from Telegram or a code is
            // typed in below.
            if (res.mfa_required) {
                beginMfaStep(res, loginUsername, password);
                return;
            }

            await completeLogin(res, loginUsername, password);
        }
    } catch (e) {
        errEl.textContent = e.message || 'Something went wrong.';
        errEl.classList.remove('hidden');
    } finally {
        submitBtn.disabled = false;
        if (!mfa.challengeId) setAuthMode(authMode);
    }
});

/* The tail of a login, shared by the ordinary path and all three second
   factor paths, so the rest of the client code never has to know which one
   produced the session. */
async function completeLogin(res, loginUsername, password) {
    session.save({
        token: res.token,
        expiresAt: res.expiresAt,
        user: res.user
    });
    currentUser = res.user;

    if (password && window.PasswordCredential) {
        try {
            const cred = new PasswordCredential({ id: loginUsername, password });
            await navigator.credentials.store(cred);
        } catch (_) {}
    }

    if (!currentUser.isApproved) {
        showToast('Your account is pending admin approval', 5000);
    } else {
        showToast(`Welcome back, ${currentUser.displayName || currentUser.username}!`);
    }
    closeModal('authModal');
    renderAuthUi();
    await loadApps();
}

/* SECOND FACTOR, mid login */

const mfa = {
    challengeId: null,
    poller: null,
    username: null,
    password: null,
    recoveryMode: false
};

function beginMfaStep(res, loginUsername, password) {
    mfa.challengeId = res.challenge_id;
    mfa.username = loginUsername;
    mfa.password = password;
    mfa.recoveryMode = false;

    $('authForm').classList.add('hidden');
    $('authFootnote').classList.add('hidden');
    document.querySelector('.auth-tabs').classList.add('hidden');
    $('authModalTitle').textContent = 'One more step';
    $('mfaStep').classList.remove('hidden');
    $('mfaMatchNumber').textContent = res.match_number ?? '--';
    $('mfaError').classList.add('hidden');
    $('mfaCodeInput').value = '';
    $('mfaCodeInput').focus();

    // If the prompt could not be delivered, say so and point at the recovery
    // codes immediately rather than leaving a spinner running.
    if (res.prompt_delivered === false) {
        $('mfaStepIntro').textContent =
            'The approval prompt could not be delivered to Telegram. Use a recovery code below.';
        setRecoveryMode(true);
    }

    mfa.poller = setInterval(pollMfa, 2500);
}

function endMfaStep() {
    clearInterval(mfa.poller);
    mfa.poller = null;
    mfa.challengeId = null;
    mfa.password = null;
    mfa.recoveryMode = false;
    $('mfaStep').classList.add('hidden');
    $('authForm').classList.remove('hidden');
    $('authFootnote').classList.remove('hidden');
    document.querySelector('.auth-tabs').classList.remove('hidden');
    setAuthMode('login');
}

function setRecoveryMode(on) {
    mfa.recoveryMode = on;
    const input = $('mfaCodeInput');
    input.value = '';
    input.maxLength = on ? 10 : 6;
    input.placeholder = on ? 'RECOVERYCD' : '123456';
    input.inputMode = on ? 'text' : 'numeric';
    $('mfaUseRecoveryBtn').textContent = on
        ? 'Use the six digit code instead'
        : 'Use a recovery code instead';
}

async function pollMfa() {
    if (!mfa.challengeId) return;
    try {
        const res = await apiFetch('/api/auth', {
            method: 'POST',
            body: { action: 'mfa_status', challenge_id: mfa.challengeId }
        });
        if (res.status === 'pending') return;
        if (res.status === 'approved') {
            const username = mfa.username;
            const password = mfa.password;
            endMfaStep();
            await completeLogin(res, username, password);
            return;
        }
        showMfaFailure(res.message || 'That sign in request is no longer valid.');
    } catch (e) {
        // A network blip while polling is not worth a scary message. A dead
        // challenge answers with a message, and that is handled above.
        if (e.status === 404 || e.status === 429) {
            showMfaFailure(e.message || 'That sign in request is no longer valid.');
        }
    }
}

function showMfaFailure(message) {
    clearInterval(mfa.poller);
    mfa.poller = null;
    $('mfaError').textContent = message;
    $('mfaError').classList.remove('hidden');
    $('mfaVerifyBtn').disabled = true;
}

$('mfaVerifyBtn').addEventListener('click', async () => {
    const value = $('mfaCodeInput').value.trim();
    if (!value) return;
    const btn = $('mfaVerifyBtn');
    btn.disabled = true;
    $('mfaError').classList.add('hidden');
    try {
        const body = mfa.recoveryMode
            ? { action: 'mfa_recover', challenge_id: mfa.challengeId, recovery_code: value }
            : { action: 'mfa_verify_code', challenge_id: mfa.challengeId, code: value };
        const res = await apiFetch('/api/auth', { method: 'POST', body });
        const username = mfa.username;
        const password = mfa.password;
        endMfaStep();
        await completeLogin(res, username, password);
    } catch (e) {
        $('mfaError').textContent = e.message || 'That did not work.';
        $('mfaError').classList.remove('hidden');
    } finally {
        btn.disabled = false;
    }
});

$('mfaCodeInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') {
        e.preventDefault();
        $('mfaVerifyBtn').click();
    }
});

$('mfaUseRecoveryBtn').addEventListener('click', () => setRecoveryMode(!mfa.recoveryMode));
$('mfaCancelBtn').addEventListener('click', endMfaStep);

$('logoutBtn').addEventListener('click', async () => {
    try {
        await apiFetch('/api/auth', {
            method: 'POST',
            body: {
                action: 'logout'
            }
        });
    } catch (_) {}
    session.clear();
    currentUser = null;
    $('userDropdown').classList.remove('open');
    renderAuthUi();
    await loadApps();
    showToast('Logged out');
});

$('avatarWrap').addEventListener('click', e => {
    e.stopPropagation();
    $('userDropdown').classList.toggle('open');
});
document.addEventListener('click', () => $('userDropdown').classList.remove('open'));

/* APPS */
async function loadApps() {
    $('gridSkeleton').classList.remove('hidden');
    document.querySelectorAll('.app-card').forEach(c => c.remove());
    $('noResults').classList.add('hidden');

    try {
        const res = await apiFetch('/api/apps');
        allApps = res.apps || [];
    } catch (_) {
        showToast('Failed to load apps');
        allApps = [];
    }

    $('gridSkeleton').classList.add('hidden');
    applyFilters();
}

function applyFilters() {
    const q = $('searchInput').value.trim().toLowerCase();
    filteredApps = allApps.filter(app => {
        const matchTag = !activeTagFilter || (app.tags || []).includes(activeTagFilter);
        const matchSearch = !q || [app.title, app.description, app.tld, ...(app.tags || [])]
            .some(s => (s || '').toLowerCase().includes(q));
        return matchTag && matchSearch;
    });

    if (activeSort === 'title_asc') filteredApps.sort((a, b) => a.title.localeCompare(b.title));
    if (activeSort === 'title_desc') filteredApps.sort((a, b) => b.title.localeCompare(a.title));
    if (activeSort === 'newest') filteredApps.sort((a, b) => new Date(b.published_date || b.created_at) - new Date(a.published_date || a.created_at));
    if (activeSort === 'oldest') filteredApps.sort((a, b) => new Date(a.published_date || a.created_at) - new Date(b.published_date || b.created_at));
    if (activeSort === 'sort_order') filteredApps.sort((a, b) => (a.sort_order ?? 999) - (b.sort_order ?? 999));

    document.querySelectorAll('.app-card').forEach(c => c.remove());
    $('noResults').classList.toggle('hidden', filteredApps.length > 0);
    filteredApps.forEach((app, i) => $('appGrid').appendChild(buildAppCard(app, i)));
}

function buildAppCard(app, i) {
    const card = document.createElement('div');
    card.className = 'app-card glass-card';
    card.style.animationDelay = `${i * 0.04}s`;

    const tld = app.tld || extractTld(app.url);
    const tags = app.tags || [];
    const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
    const appDate = new Date(app.published_date || app.created_at);
    const isNew = app.published_date || app.created_at ? appDate >= sevenDaysAgo : false;

    card.innerHTML = `
    ${isNew ? '<span class="app-card-new-badge">NEW</span>' : ''}
    ${app.thumbnail_url
      ? `<img class="app-card-thumb" src="${escHtml(app.thumbnail_url)}" alt="${escHtml(app.title)}" loading="lazy" />`
      : `<div class="app-card-thumb-placeholder"><svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="opacity:.4"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg></div>`}
    <div class="app-card-body">
      <h3 class="app-card-title">${escHtml(app.title)}</h3>
      ${app.description ? `<p class="app-card-desc">${escHtml(app.description)}</p>` : ''}
      <div class="app-card-pills">
        <span class="pill pill-tld">${escHtml(tld)}</span>
        ${tags.map(t => `<span class="pill ${tagClass(t)}">${escHtml(t)}</span>`).join('')}
        ${!app.published && canContribute() ? `<span class="pill pill-draft">Draft</span>` : ''}
      </div>
    </div>`;

    card.addEventListener('click', () => openAppModal(app));
    return card;
}

/* APP DETAIL MODAL */
function openAppModal(app) {
    $('appModalTitle').textContent = app.title;
    $('appModalDesc').textContent = app.description || '';
    $('appModalLink').href = app.url;

    const updatedAt = app.updated_at || app.created_at;
    $('appModalUpdated').textContent = updatedAt ? `Last Updated: ${formatDate(updatedAt)}` : '';

    const tld = app.tld || extractTld(app.url);
    const tags = app.tags || [];
    $('appModalTld').innerHTML = `<span class="pill pill-tld">${escHtml(tld)}</span>`;
    $('appModalTags').innerHTML = tags.map(t => `<span class="pill ${tagClass(t)}">${escHtml(t)}</span>`).join('');

    const gallery = app.gallery_urls?.length ? app.gallery_urls : (app.thumbnail_url ? [app.thumbnail_url] : []);
    const mainImg = $('galleryMainImg');
    const thumbsEl = $('galleryThumbs');
    thumbsEl.innerHTML = '';

    let currentBlobUrl = null;
    const showImg = url => {
        mainImg.style.opacity = '0';
        if (currentBlobUrl) { URL.revokeObjectURL(currentBlobUrl); currentBlobUrl = null; }
        fetch(url)
            .then(r => r.blob())
            .then(blob => {
                currentBlobUrl = URL.createObjectURL(blob);
                setTimeout(() => {
                    mainImg.src = currentBlobUrl;
                    mainImg.alt = app.title;
                    mainImg.style.opacity = '1';
                }, 150);
            })
            .catch(() => {
                setTimeout(() => {
                    mainImg.src = url;
                    mainImg.alt = app.title;
                    mainImg.style.opacity = '1';
                }, 150);
            });
    };
    mainImg.style.cursor = 'pointer';
    mainImg.onclick = () => {
        if (currentBlobUrl) window.open(currentBlobUrl, '_blank');
    };

    if (gallery.length) {
        const start = Math.min(app.thumbnail_index || 0, gallery.length - 1);
        showImg(gallery[start]);
        if (gallery.length > 1) {
            gallery.forEach((url, idx) => {
                const t = document.createElement('div');
                t.className = `gallery-thumb ${idx === start ? 'active' : ''}`;
                t.innerHTML = `<img src="${escHtml(url)}" alt="Screenshot ${idx+1}" loading="lazy" />`;
                t.addEventListener('click', () => {
                    showImg(url);
                    thumbsEl.querySelectorAll('.gallery-thumb').forEach((th, i) => th.classList.toggle('active', i === idx));
                });
                thumbsEl.appendChild(t);
            });
        }
    } else {
        mainImg.src = '';
        mainImg.alt = '';
    }

    const actions = $('appModalActions');
    if (canContribute()) {
        actions.classList.remove('hidden');
        $('editAppBtn').onclick = () => {
            closeModal('appModal');
            openEditModal(app);
        };
        $('deleteAppBtn').onclick = () => {
            pendingDeleteId = app.id;
            openModal('confirmModal');
        };
    } else {
        actions.classList.add('hidden');
    }

    openModal('appModal');
}

/* ADD / EDIT APP MODAL */
$('addAppBtn').addEventListener('click', () => openEditModal(null));

function openEditModal(app) {
    editingAppId = app?.id || null;
    $('editModalTitle').textContent = app ? 'Edit App' : 'Add App';
    $('editError').classList.add('hidden');
    $('editTitle').value = app?.title || '';
    $('editUrl').value = app?.url || '';
    $('editDesc').value = app?.description || '';
    $('editPublished').checked = app?.published ?? false;
    $('editPublishedDate').value = app?.published_date ? app.published_date.split('T')[0] : '';
    document.querySelectorAll('[name="tag"]').forEach(cb => cb.checked = (app?.tags || []).includes(cb.value));
    galleryFiles = [];
    galleryUrls = app?.gallery_urls ? [...app.gallery_urls] : [];
    selectedThumbIndex = app?.thumbnail_index ?? 0;
    renderGalleryPicker();
    openModal('editModal');
}

/* Drop zone */
const dropZone = $('dropZone');
const fileInput = $('imageFileInput');

$('browseBtn').addEventListener('click', () => fileInput.click());
dropZone.addEventListener('click', e => {
    if (!e.target.classList.contains('link-btn')) fileInput.click();
});
dropZone.addEventListener('dragover', e => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    handleNewFiles([...e.dataTransfer.files].filter(f => f.type.startsWith('image/')));
});
fileInput.addEventListener('change', () => {
    handleNewFiles([...fileInput.files]);
    fileInput.value = '';
});

function handleNewFiles(files) {
    galleryFiles.push(...files);
    renderGalleryPicker();
}

function renderGalleryPicker() {
    const picker = $('galleryPicker');
    picker.innerHTML = '';

    galleryUrls.forEach((url, idx) => {
        const item = document.createElement('div');
        item.className = `picker-item ${idx === selectedThumbIndex ? 'selected' : ''}`;
        item.innerHTML = `<img src="${escHtml(url)}" alt="Image ${idx+1}" /><span class="picker-badge">Cover</span><button type="button" class="picker-remove" aria-label="Remove"><svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>`;
        item.addEventListener('click', e => {
            if (e.target.classList.contains('picker-remove')) {
                galleryUrls.splice(idx, 1);
                if (selectedThumbIndex >= galleryUrls.length + galleryFiles.length) selectedThumbIndex = 0;
                renderGalleryPicker();
                return;
            }
            selectedThumbIndex = idx;
            renderGalleryPicker();
        });
        picker.appendChild(item);
    });

    galleryFiles.forEach((file, fi) => {
        const totalIdx = galleryUrls.length + fi;
        const url = URL.createObjectURL(file);
        const item = document.createElement('div');
        item.className = `picker-item ${totalIdx === selectedThumbIndex ? 'selected' : ''}`;
        item.innerHTML = `<img src="${url}" alt="New ${fi+1}" /><span class="picker-badge">Cover</span><button type="button" class="picker-remove" aria-label="Remove"><svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>`;
        item.addEventListener('click', e => {
            if (e.target.classList.contains('picker-remove')) {
                galleryFiles.splice(fi, 1);
                if (selectedThumbIndex >= galleryUrls.length + galleryFiles.length) selectedThumbIndex = 0;
                renderGalleryPicker();
                return;
            }
            selectedThumbIndex = totalIdx;
            renderGalleryPicker();
        });
        picker.appendChild(item);
    });
}

/* Canvas WebP compression */
async function compressToWebP(file, maxWidth = 1200, quality = 0.82) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        const url = URL.createObjectURL(file);
        img.onload = () => {
            let {
                width,
                height
            } = img;
            if (width > maxWidth) {
                height = Math.round(height * maxWidth / width);
                width = maxWidth;
            }
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            canvas.getContext('2d').drawImage(img, 0, 0, width, height);
            URL.revokeObjectURL(url);
            canvas.toBlob(b => b ? resolve(b) : reject(new Error('Compression failed')), 'image/webp', quality);
        };
        img.onerror = reject;
        img.src = url;
    });
}

async function uploadFiles(files, appId) {
    const urls = [];
    for (const file of files) {
        const blob = await compressToWebP(file);
        const safeName = file.name.replace(/\.[^.]+$/, '') + '.webp';
        const signed = await apiFetch('/api/upload', {
            method: 'POST',
            body: {
                appId,
                fileName: safeName
            }
        });
        const uploadRes = await fetch(signed.signedUrl, {
            method: 'PUT',
            headers: {
                'Content-Type': 'image/webp'
            },
            body: blob
        });
        if (!uploadRes.ok) throw new Error('Image upload failed');
        urls.push(signed.publicUrl);
    }
    return urls;
}

$('editForm').addEventListener('submit', async e => {
    e.preventDefault();
    const errEl = $('editError');
    errEl.classList.add('hidden');
    const title = $('editTitle').value.trim();
    const url = $('editUrl').value.trim();
    if (!title || !url) {
        errEl.textContent = 'Title and URL are required.';
        errEl.classList.remove('hidden');
        return;
    }

    const submitBtn = $('editSubmit');
    submitBtn.textContent = 'Saving…';
    submitBtn.disabled = true;

    try {
        const appId = editingAppId || crypto.randomUUID();
        const tags = [...document.querySelectorAll('[name="tag"]:checked')].map(c => c.value);
        let newUrls = [];
        if (galleryFiles.length) newUrls = await uploadFiles(galleryFiles, appId);

        const finalGallery = [...galleryUrls, ...newUrls];
        const thumbIdx = Math.min(selectedThumbIndex, Math.max(0, finalGallery.length - 1));

        const payload = {
            title,
            url,
            description: $('editDesc').value.trim() || null,
            tags,
            galleryUrls: finalGallery,
            thumbnailIndex: thumbIdx,
            published: $('editPublished').checked,
            publishedDate: $('editPublishedDate').value || null
        };

        if (editingAppId) {
            await apiFetch(`/api/apps?id=${editingAppId}`, {
                method: 'PUT',
                body: payload
            });
        } else {
            await apiFetch('/api/apps', {
                method: 'POST',
                body: {
                    id: appId,
                    ...payload
                }
            });
        }

        closeModal('editModal');
        showToast(editingAppId ? 'App updated ✓' : 'App added ✓');
        await loadApps();
    } catch (e) {
        errEl.textContent = e.message || 'Save failed.';
        errEl.classList.remove('hidden');
    } finally {
        submitBtn.textContent = 'Save App';
        submitBtn.disabled = false;
    }
});

/* DELETE */
$('confirmDeleteBtn').addEventListener('click', async () => {
    if (!pendingDeleteId) return;
    try {
        await apiFetch(`/api/apps?id=${pendingDeleteId}`, {
            method: 'DELETE'
        });
        pendingDeleteId = null;
        closeModal('confirmModal');
        closeModal('appModal');
        showToast('App deleted');
        await loadApps();
    } catch (e) {
        showToast('Delete failed: ' + e.message);
    }
});

/* ADMIN PANEL */
$('adminPanelBtn').addEventListener('click', async () => {
    $('userDropdown').classList.remove('open');
    await loadAdminData();
    openModal('adminModal');
});

document.querySelectorAll('.admin-tab').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.admin-panel').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        const key = btn.dataset.panel;
        $('panel' + key.charAt(0).toUpperCase() + key.slice(1)).classList.add('active');
        // Settings holds live state rather than a table loaded with the others,
        // so it fetches when it is opened.
        if (key === 'Settings') loadTelegramSettings();
    });
});

async function loadAdminData() {
    try {
        const res = await apiFetch('/api/users');
        const all = res.users || [];
        renderPendingTable(all.filter(u => !u.isApproved));
        renderUsersTable(all.filter(u => u.isApproved));
        renderPreapprovedTable(res.preapproved || []);
    } catch (e) {
        showToast('Could not load users: ' + e.message);
    }
}

function renderPendingTable(users) {
    const tbody = $('pendingTable').querySelector('tbody');
    tbody.innerHTML = '';
    $('pendingEmpty').classList.toggle('hidden', users.length > 0);
    users.forEach(u => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
      <td>${escHtml(u.displayName||'-')}</td>
      <td>${escHtml(u.email)}</td>
      <td>${formatDate(u.created_at||'')}</td>
      <td style="display:flex;gap:.4rem;padding:.65rem .75rem">
        <button class="btn btn-primary" style="font-size:.75rem;padding:.3rem .7rem" data-uid="${u.id}" data-action="approve-editor">Editor</button>
        <button class="btn btn-ghost"   style="font-size:.75rem;padding:.3rem .7rem" data-uid="${u.id}" data-action="approve-admin">Admin</button>
        <button class="btn btn-danger"  style="font-size:.75rem;padding:.3rem .7rem" data-uid="${u.id}" data-action="reject">Reject</button>
      </td>`;
        tbody.appendChild(tr);
    });
}

function renderUsersTable(users) {
    const tbody = $('usersTable').querySelector('tbody');
    tbody.innerHTML = '';
    $('usersEmpty').classList.toggle('hidden', users.length > 0);
    users.forEach(u => {
        const isMe = u.id === currentUser?.id;
        const roleTxt = u.isAdmin ? 'admin' : u.isEditor ? 'editor' : 'viewer';
        const tr = document.createElement('tr');
        tr.innerHTML = `
      <td>${escHtml(u.displayName||'-')}</td>
      <td>${escHtml(u.email)}</td>
      <td><span class="role-badge">${roleTxt}</span></td>
      <td style="display:flex;gap:.4rem;padding:.65rem .75rem">
        ${!isMe ? `
          <button class="btn btn-ghost"  style="font-size:.75rem;padding:.3rem .7rem" data-uid="${u.id}" data-action="toggle-role" data-is-admin="${u.isAdmin}">${u.isAdmin ? '↓ Editor' : '↑ Admin'}</button>
          <button class="btn btn-danger" style="font-size:.75rem;padding:.3rem .7rem" data-uid="${u.id}" data-action="revoke">Revoke</button>
        ` : '<span style="font-size:.8rem;color:var(--muted)">You</span>'}
      </td>`;
        tbody.appendChild(tr);
    });
}

function renderPreapprovedTable(rows) {
    const tbody = $('preapprovedTable')?.querySelector('tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    const empty = $('preapprovedEmpty');
    if (empty) empty.classList.toggle('hidden', rows.length > 0);

    rows.forEach(r => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
      <td>${escHtml(r.email)}</td>
      <td><span class="role-badge">${escHtml(r.preapproved_role)}</span></td>
      <td>${r.activated_at ? '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg> Activated' : '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> Pending'}</td>
      <td style="padding:.65rem .75rem">
        ${!r.activated_at ? `<button class="btn btn-danger" style="font-size:.75rem;padding:.3rem .7rem" data-prid="${r.id}" data-action="remove-preapproval">Remove</button>` : '-'}
      </td>`;
        tbody.appendChild(tr);
    });
}

// Preapprove form
$('preapproveForm')?.addEventListener('submit', async e => {
    e.preventDefault();
    const email = $('preapproveEmail').value.trim();
    const role = $('preapproveRole').value;
    if (!email) return;
    try {
        const res = await apiFetch('/api/users?action=preapprove', {
            method: 'POST',
            body: {
                email,
                role
            }
        });
        showToast(res.directApproval ? 'User approved directly ✓' : 'Preapproval added ✓');
        $('preapproveEmail').value = '';
        await loadAdminData();
    } catch (e) {
        showToast('Error: ' + e.message);
    }
});

$('adminModal').addEventListener('click', async e => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const uid = btn.dataset.uid;
    const prid = btn.dataset.prid;
    const action = btn.dataset.action;

    try {
        if (action === 'approve-editor') {
            await apiFetch(`/api/users?id=${uid}`, {
                method: 'PUT',
                body: {
                    isApproved: true,
                    isEditor: true,
                    isAdmin: false
                }
            });
        } else if (action === 'approve-admin') {
            await apiFetch(`/api/users?id=${uid}`, {
                method: 'PUT',
                body: {
                    isApproved: true,
                    isEditor: true,
                    isAdmin: true
                }
            });
        } else if (action === 'reject') {
            await apiFetch(`/api/users?id=${uid}`, {
                method: 'DELETE'
            });
        } else if (action === 'toggle-role') {
            const currentlyAdmin = btn.dataset.isAdmin === 'true';
            await apiFetch(`/api/users?id=${uid}`, {
                method: 'PUT',
                body: {
                    isAdmin: !currentlyAdmin,
                    isEditor: true
                }
            });
        } else if (action === 'revoke') {
            await apiFetch(`/api/users?id=${uid}`, {
                method: 'PUT',
                body: {
                    isApproved: false,
                    isEditor: false,
                    isAdmin: false
                }
            });
        } else if (action === 'remove-preapproval') {
            await apiFetch(`/api/users?action=preapprove&id=${prid}`, {
                method: 'DELETE'
            });
        }
        showToast('Updated ✓');
        await loadAdminData();
    } catch (e) {
        showToast('Action failed: ' + e.message);
    }
});

/* SEARCH / FILTER / SORT */
$('searchInput').addEventListener('input', applyFilters);
$('sortSelect').addEventListener('change', () => {
    activeSort = $('sortSelect').value;
    applyFilters();
});

document.querySelectorAll('[data-filter="tag"]').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('[data-filter="tag"]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeTagFilter = btn.dataset.value;
        applyFilters();
    });
});

/* MODAL CLOSE */
document.querySelectorAll('.modal-close').forEach(btn => {
    btn.addEventListener('click', () => {
        if (btn.dataset.modal) closeModal(btn.dataset.modal);
    });
});
document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
    backdrop.addEventListener('click', e => {
        if (e.target === backdrop) backdrop.classList.add('hidden');
    });
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape')
        document.querySelectorAll('.modal-backdrop:not(.hidden)').forEach(m => m.classList.add('hidden'));
});

/* PWA */
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}

/* =========================================================
   SETTINGS TAB, Telegram linking and the second factor
   =========================================================
   The bot can redeem a linking code but can never create one, because minting
   needs a signed in portal session. This tab is that entry point. Unlinking
   also lives here and nowhere else, so detaching an account costs the portal
   session as well as the chat. */

const tg = {
    state: null,
    code: null,
    codeExpiresAt: null,
    poller: null,
    countdown: null,
    enrollChallengeId: null,
    enrollPoller: null,
    unlinkExpiresAt: null,
    unlinkCountdown: null,
    passwordIntent: null
};

async function telegramFetch(body) {
    return apiFetch('/api/telegram', { method: 'POST', body });
}

function showOnly(ids, visible) {
    ids.forEach(id => $(id).classList.toggle('hidden', id !== visible));
}

function stopLinkPolling() {
    clearInterval(tg.poller);
    clearInterval(tg.countdown);
    tg.poller = null;
    tg.countdown = null;
}

async function loadTelegramSettings() {
    try {
        tg.state = await telegramFetch({ action: 'status' });
    } catch (e) {
        showToast('Could not load the Telegram settings: ' + e.message);
        return;
    }
    renderTelegramSettings();
}

function renderTelegramSettings() {
    const state = tg.state || { linked: false, mfaEnabled: false };

    if (state.linked) {
        stopLinkPolling();
        showOnly(['tgUnlinked', 'tgWaiting', 'tgLinked'], 'tgLinked');
        $('tgAccount').textContent = state.telegramUsername
            ? '@' + state.telegramUsername
            : 'Telegram id ' + state.telegramId;
        $('tgLinkedAt').textContent = formatDate(state.linkedAt || '');
    } else if (tg.code) {
        showOnly(['tgUnlinked', 'tgWaiting', 'tgLinked'], 'tgWaiting');
    } else {
        showOnly(['tgUnlinked', 'tgWaiting', 'tgLinked'], 'tgUnlinked');
    }

    // The second factor only makes sense once an account is linked.
    const toggle = $('mfaToggle');
    toggle.disabled = !state.linked;
    toggle.checked = !!state.mfaEnabled;
    $('mfaToggleLabel').textContent = state.mfaEnabled ? 'On' : 'Off';
    $('mfaHint').textContent = state.linked
        ? 'With this on, a correct password alone no longer signs you in. The sign in is held until you approve it from the linked chat, or type the code that arrives with it.'
        : 'Link Telegram above first. The second factor needs somewhere to send the approval.';

    $('mfaOnDetail').classList.toggle('hidden', !state.mfaEnabled);
    if (state.mfaEnabled) {
        $('mfaRecoveryCount').textContent = 'Recovery codes left: ' + (state.recoveryCodesLeft ?? 0);
        renderMfaAudit(state.recentApprovals || []);
    }

    // Unlinking while the second factor is on would be a password free way to
    // strip it off a hijacked session, so the two stay separate and ordered.
    $('tgUnlinkBtn').disabled = !!state.mfaEnabled;
    $('tgUnlinkBtn').title = state.mfaEnabled ? 'Turn two factor authentication off first' : '';
}

function renderMfaAudit(rows) {
    const tbody = $('mfaAuditTable').querySelector('tbody');
    tbody.innerHTML = '';
    $('mfaAuditEmpty').classList.toggle('hidden', rows.length > 0);
    rows.forEach(row => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
      <td>${formatDate(row.resolved_at || row.created_at || '')}</td>
      <td>${escHtml(row.device || '-')}</td>
      <td><span class="role-badge">${escHtml(row.status)}</span></td>`;
        tbody.appendChild(tr);
    });
}

/* Linking */

async function requestLinkCode(openTab) {
    try {
        const res = await telegramFetch({ action: 'issue_code' });
        tg.code = res.code;
        tg.codeExpiresAt = new Date(res.expiresAt);
        $('tgCode').textContent = res.code;
        renderTelegramSettings();
        startLinkWatch();
        // The deep link carries the code as the start payload, which is what
        // turns a plain Start press into a redeemed link.
        if (openTab) window.open(res.deepLink, '_blank', 'noopener');
    } catch (e) {
        showToast('Could not make a code: ' + e.message);
    }
}

function startLinkWatch() {
    stopLinkPolling();
    tg.poller = setInterval(async () => {
        try {
            const res = await telegramFetch({ action: 'status' });
            if (res.linked) {
                tg.state = res;
                tg.code = null;
                showToast('Telegram is linked');
                renderTelegramSettings();
            }
        } catch (_) {}
    }, 4000);

    tg.countdown = setInterval(() => {
        const left = Math.max(0, Math.round((tg.codeExpiresAt - Date.now()) / 1000));
        $('tgCountdown').textContent = left
            ? 'This code expires in ' + Math.floor(left / 60) + 'm ' + String(left % 60).padStart(2, '0') + 's.'
            : 'This code has expired. Generate a new one.';
        if (!left) stopLinkPolling();
    }, 1000);
}

$('tgLinkBtn').addEventListener('click', () => requestLinkCode(true));
$('tgNewCodeBtn').addEventListener('click', () => requestLinkCode(true));
$('tgCancelWaitBtn').addEventListener('click', () => {
    stopLinkPolling();
    tg.code = null;
    renderTelegramSettings();
});

// A code consumed in the other tab shows up the moment this one comes back.
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && tg.code && !$('adminModal').classList.contains('hidden')) {
        loadTelegramSettings();
    }
});

/* Unlinking, three deliberate presses */

$('tgUnlinkBtn').addEventListener('click', () => {
    $('unlinkStep1').classList.remove('hidden');
    $('unlinkStep2').classList.add('hidden');
    $('unlinkError').classList.add('hidden');
    $('unlinkCode').value = '';
    openModal('unlinkModal');
});

$('unlinkContinueBtn').addEventListener('click', async () => {
    const btn = $('unlinkContinueBtn');
    btn.disabled = true;
    try {
        const res = await telegramFetch({ action: 'unlink_request' });
        tg.unlinkExpiresAt = new Date(res.expiresAt);
        $('unlinkStep1').classList.add('hidden');
        $('unlinkStep2').classList.remove('hidden');
        $('unlinkCode').focus();
        startUnlinkCountdown();
    } catch (e) {
        showToast(e.message || 'Could not send the confirmation code');
    } finally {
        btn.disabled = false;
    }
});

function startUnlinkCountdown() {
    clearInterval(tg.unlinkCountdown);
    const sentAt = Date.now();
    tg.unlinkCountdown = setInterval(() => {
        const left = Math.max(0, Math.round((tg.unlinkExpiresAt - Date.now()) / 1000));
        $('unlinkCountdown').textContent = left
            ? 'The code expires in ' + Math.floor(left / 60) + 'm ' + String(left % 60).padStart(2, '0') + 's.'
            : 'That code has expired. Send a new one.';
        // Only worth resending once the first one is old enough to be lost.
        $('unlinkResendBtn').disabled = (Date.now() - sentAt) < 45000;
        if (!left) clearInterval(tg.unlinkCountdown);
    }, 1000);
}

$('unlinkResendBtn').addEventListener('click', () => $('unlinkContinueBtn').click());

$('unlinkConfirmBtn').addEventListener('click', async () => {
    const btn = $('unlinkConfirmBtn');
    btn.disabled = true;
    $('unlinkError').classList.add('hidden');
    try {
        await telegramFetch({ action: 'unlink', code: $('unlinkCode').value.trim() });
        clearInterval(tg.unlinkCountdown);
        closeModal('unlinkModal');
        showToast('Telegram is no longer linked');
        tg.code = null;
        await loadTelegramSettings();
    } catch (e) {
        $('unlinkError').textContent = e.message || 'That did not work.';
        $('unlinkError').classList.remove('hidden');
    } finally {
        btn.disabled = false;
    }
});

/* The second factor */

$('mfaToggle').addEventListener('change', async e => {
    if (e.target.checked) {
        await startEnrolment();
    } else {
        // Put it back until the password confirms, so the control never lies
        // about the state of the account.
        e.target.checked = true;
        askForPassword('disable', 'Enter your password to turn two factor authentication off.');
    }
});

async function startEnrolment() {
    $('mfaToggle').disabled = true;
    try {
        const res = await telegramFetch({ action: 'mfa_enroll' });
        tg.enrollChallengeId = res.challenge_id;
        $('mfaEnrollMatch').textContent = res.match_number;
        $('mfaEnrolling').classList.remove('hidden');
        // The toggle only flips once the test approval comes back, so nobody
        // locks themselves out of an account whose chat is broken or blocked.
        tg.enrollPoller = setInterval(pollEnrolment, 2500);
    } catch (e) {
        $('mfaToggle').checked = false;
        $('mfaToggle').disabled = false;
        showToast(e.message || 'Could not start the test approval');
    }
}

function stopEnrolment() {
    clearInterval(tg.enrollPoller);
    tg.enrollPoller = null;
    tg.enrollChallengeId = null;
    $('mfaEnrolling').classList.add('hidden');
    $('mfaToggle').disabled = false;
}

async function pollEnrolment() {
    if (!tg.enrollChallengeId) return;
    try {
        const res = await telegramFetch({
            action: 'mfa_enroll_status',
            challenge_id: tg.enrollChallengeId
        });
        if (res.status === 'pending') return;
        stopEnrolment();
        if (res.status !== 'approved') {
            $('mfaToggle').checked = false;
            showToast('The test approval was not completed, so nothing changed');
            return;
        }
        if (res.recoveryCodes) revealRecoveryCodes(res.recoveryCodes);
        await loadTelegramSettings();
    } catch (e) {
        stopEnrolment();
        $('mfaToggle').checked = false;
        showToast(e.message || 'The test approval could not be confirmed');
    }
}

$('mfaEnrollCancelBtn').addEventListener('click', () => {
    stopEnrolment();
    $('mfaToggle').checked = false;
});

function revealRecoveryCodes(codes) {
    $('mfaRecoveryCodes').textContent = codes.join('\n');
    $('mfaRecoveryReveal').classList.remove('hidden');
}

$('mfaCopyCodesBtn').addEventListener('click', async () => {
    try {
        await navigator.clipboard.writeText($('mfaRecoveryCodes').textContent);
        showToast('Recovery codes copied');
    } catch (_) {
        showToast('Select the codes and copy them by hand');
    }
});

$('mfaSavedCodesBtn').addEventListener('click', () => {
    $('mfaRecoveryCodes').textContent = '';
    $('mfaRecoveryReveal').classList.add('hidden');
});

$('mfaRegenBtn').addEventListener('click', () =>
    askForPassword('regenerate', 'Enter your password to replace every recovery code.')
);

function askForPassword(intent, prompt) {
    tg.passwordIntent = intent;
    $('mfaPasswordPrompt').textContent = prompt;
    $('mfaPassword').value = '';
    $('mfaPasswordError').classList.add('hidden');
    $('mfaPasswordRow').classList.remove('hidden');
    $('mfaPassword').focus();
}

function closePasswordRow() {
    tg.passwordIntent = null;
    $('mfaPassword').value = '';
    $('mfaPasswordRow').classList.add('hidden');
}

$('mfaPasswordCancelBtn').addEventListener('click', () => {
    closePasswordRow();
    renderTelegramSettings();
});

$('mfaPassword').addEventListener('keydown', e => {
    if (e.key === 'Enter') {
        e.preventDefault();
        $('mfaPasswordConfirmBtn').click();
    }
});

$('mfaPasswordConfirmBtn').addEventListener('click', async () => {
    const password = $('mfaPassword').value;
    if (!password) return;
    const btn = $('mfaPasswordConfirmBtn');
    btn.disabled = true;
    $('mfaPasswordError').classList.add('hidden');
    try {
        if (tg.passwordIntent === 'disable') {
            await telegramFetch({ action: 'mfa_disable', password });
            closePasswordRow();
            showToast('Two factor authentication is off');
            await loadTelegramSettings();
        } else if (tg.passwordIntent === 'regenerate') {
            const res = await telegramFetch({ action: 'mfa_regenerate_recovery', password });
            closePasswordRow();
            revealRecoveryCodes(res.recoveryCodes);
            await loadTelegramSettings();
        }
    } catch (e) {
        $('mfaPasswordError').textContent = e.message || 'That did not work.';
        $('mfaPasswordError').classList.remove('hidden');
    } finally {
        btn.disabled = false;
    }
});
