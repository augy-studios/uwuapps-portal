'use strict';

/* SESSION — localStorage key: uwusuite_session
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

/* ── API FETCH — always sends Bearer token if present */
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
            message: data.error || 'Session expired — please log in again'
        };
    }

    if (!data.ok) throw {
        status: res.status,
        message: data.error || 'Request failed'
    };
    return data;
}

/* ── STATE */
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

/* ── DOM HELPERS */
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
        bots: 'pill-bots'
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

/* ── THEME */
const THEME_KEY = 'uwusuite-theme';
const themeColors = {
    classic: '#ccffcc',
    notgreen1: '#ffcccc',
    notgreen2: '#ccccff',
    notgreen3: '#ffffcc',
    notgreen4: '#ffccff',
    notgreen5: '#ccffff',
    ultralight: '#f8fff8'
};

function applyTheme(key) {
    document.documentElement.setAttribute('data-theme', key);
    document.querySelector('meta[name="theme-color"]').content = themeColors[key] || '#ccffcc';
    localStorage.setItem(THEME_KEY, key);
    document.querySelectorAll('.theme-swatch').forEach(s =>
        s.classList.toggle('active', s.dataset.theme === key)
    );
}
applyTheme(localStorage.getItem(THEME_KEY) || 'classic');

$('themeBtn').addEventListener('click', () => openModal('themeModal'));
document.querySelectorAll('.theme-swatch').forEach(btn =>
    btn.addEventListener('click', () => {
        applyTheme(btn.dataset.theme);
        closeModal('themeModal');
    })
);

/* ── AUTH UI */
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

/* ── BOOT */
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

/* ── AUTH MODAL */
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
            session.save({
                token: res.token,
                expiresAt: res.expiresAt,
                user: res.user
            });
            currentUser = res.user;

            if (window.PasswordCredential) {
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
    } catch (e) {
        errEl.textContent = e.message || 'Something went wrong.';
        errEl.classList.remove('hidden');
    } finally {
        submitBtn.disabled = false;
        setAuthMode(authMode);
    }
});

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

/* ── APPS */
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
    if (activeSort === 'newest') filteredApps.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    if (activeSort === 'oldest') filteredApps.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
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

    card.innerHTML = `
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

/* ── APP DETAIL MODAL */
function openAppModal(app) {
    $('appModalTitle').textContent = app.title;
    $('appModalDesc').textContent = app.description || '';
    $('appModalLink').href = app.url;

    const tld = app.tld || extractTld(app.url);
    const tags = app.tags || [];
    $('appModalTld').innerHTML = `<span class="pill pill-tld">${escHtml(tld)}</span>`;
    $('appModalTags').innerHTML = tags.map(t => `<span class="pill ${tagClass(t)}">${escHtml(t)}</span>`).join('');

    const gallery = app.gallery_urls?.length ? app.gallery_urls : (app.thumbnail_url ? [app.thumbnail_url] : []);
    const mainImg = $('galleryMainImg');
    const thumbsEl = $('galleryThumbs');
    thumbsEl.innerHTML = '';

    const showImg = url => {
        mainImg.style.opacity = '0';
        setTimeout(() => {
            mainImg.src = url;
            mainImg.alt = app.title;
            mainImg.style.opacity = '1';
        }, 150);
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

/* ── ADD / EDIT APP MODAL */
$('addAppBtn').addEventListener('click', () => openEditModal(null));

function openEditModal(app) {
    editingAppId = app?.id || null;
    $('editModalTitle').textContent = app ? 'Edit App' : 'Add App';
    $('editError').classList.add('hidden');
    $('editTitle').value = app?.title || '';
    $('editUrl').value = app?.url || '';
    $('editDesc').value = app?.description || '';
    $('editPublished').checked = app?.published ?? false;
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
            published: $('editPublished').checked
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

/* ── DELETE */
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

/* ── ADMIN PANEL */
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
      <td>${escHtml(u.displayName||'—')}</td>
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
      <td>${escHtml(u.displayName||'—')}</td>
      <td>${escHtml(u.email)}</td>
      <td><span class="role-badge">${roleTxt}</span></td>
      <td style="display:flex;gap:.4rem;padding:.65rem .75rem">
        ${!isMe ? `
          <button class="btn btn-ghost"  style="font-size:.75rem;padding:.3rem .7rem" data-uid="${u.id}" data-action="toggle-role" data-is-admin="${u.isAdmin}">${u.isAdmin ? '↓ Editor' : '↑ Admin'}</button>
          <button class="btn btn-danger" style="font-size:.75rem;padding:.3rem .7rem" data-uid="${u.id}" data-action="revoke">Revoke</button>
        ` : '<span style="font-size:.8rem;color:var(--text-muted)">You</span>'}
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
        ${!r.activated_at ? `<button class="btn btn-danger" style="font-size:.75rem;padding:.3rem .7rem" data-prid="${r.id}" data-action="remove-preapproval">Remove</button>` : '—'}
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

/* ── SEARCH / FILTER / SORT */
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

/* ── MODAL CLOSE */
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

/* ── PWA */
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}