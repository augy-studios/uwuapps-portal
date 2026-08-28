// ============================================================================
// sw.template.js — Universal PWA Service Worker Template
// Replace APP_NAME, CACHE_PREFIX, STATIC_ASSETS, and icon paths.
// Covers: Offline Support, Push Notifications, Background Sync, Periodic Sync
// ============================================================================

const APP_NAME = "MY_APP";               // ← change this
const CACHE_VERSION = `${APP_NAME}-v1`;
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const DYNAMIC_CACHE = `${CACHE_VERSION}-dynamic`;
const OFFLINE_URL = "/offline.html";

// Files to precache on install
const STATIC_ASSETS = [
  "/",
  "/index.html",
  "/style.css",
  "/script.js",
  "/icon-192.png",                       // ← change to your icon paths
  "/icon-512.png",
  "/favicon.ico",
  "/manifest.json",
  OFFLINE_URL
];

// ─── Install ──────────────────────────────────────────────────────────────────
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// ─── Activate ─────────────────────────────────────────────────────────────────
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => !key.startsWith(CACHE_VERSION))
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

// ─── Fetch — Offline Support ──────────────────────────────────────────────────
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" || !event.request.url.startsWith("http")) return;

  // API routes: network-first, JSON error when offline
  if (event.request.url.includes("/api/")) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(JSON.stringify({ error: "You are offline" }), {
          status: 503,
          headers: { "Content-Type": "application/json" }
        })
      )
    );
    return;
  }

  // Everything else: cache-first, network fallback, offline page for HTML
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const networkFetch = fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(DYNAMIC_CACHE).then((cache) => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => {
          if (event.request.headers.get("Accept")?.includes("text/html")) {
            return caches.match(OFFLINE_URL);
          }
          return new Response("Offline", { status: 503 });
        });
      return cached || networkFetch;
    })
  );
});

// ─── Push Notifications ───────────────────────────────────────────────────────
// App-side registration (paste in your main JS):
//
//   const reg = await navigator.serviceWorker.ready;
//   const sub = await reg.pushManager.subscribe({
//     userVisibleOnly: true,
//     applicationServerKey: YOUR_VAPID_PUBLIC_KEY  // from web-push library
//   });
//   await fetch("/api/push/subscribe", { method: "POST", body: JSON.stringify(sub) });

self.addEventListener("push", (event) => {
  let data = {
    title: APP_NAME,
    body: "You have a new notification!",
    icon: "/icon-192.png",              // ← change to your icon
    badge: "/icon-192.png",
    tag: `${APP_NAME}-notification`
  };

  if (event.data) {
    try {
      data = { ...data, ...event.data.json() };
    } catch {
      data.body = event.data.text();
    }
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: data.icon,
      badge: data.badge,
      tag: data.tag,
      data: data.url ? { url: data.url } : undefined,
      requireInteraction: data.requireInteraction || false
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/";
  event.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((list) => {
        for (const client of list) {
          if (client.url === url && "focus" in client) return client.focus();
        }
        return clients.openWindow(url);
      })
  );
});

// ─── Background Sync ──────────────────────────────────────────────────────────
// App-side usage (queue a failed request, then register sync):
//
//   const db = await openSyncDB();
//   await queueRequest(db, { url: "/api/save", method: "POST", body: JSON.stringify(data) });
//   const reg = await navigator.serviceWorker.ready;
//   await reg.sync.register("sync-pending-actions");

self.addEventListener("sync", (event) => {
  if (event.tag === "sync-pending-actions") {
    event.waitUntil(syncPendingActions());
  }
});

async function syncPendingActions() {
  const db = await openDB();
  const pending = await getAllPending(db);
  for (const item of pending) {
    try {
      await fetch(item.url, {
        method: item.method,
        headers: item.headers ? JSON.parse(item.headers) : undefined,
        body: item.body || undefined
      });
      await deletePending(db, item.id);
    } catch {
      // Will retry on next sync — browser handles back-off automatically
    }
  }
}

// ─── Periodic Sync ────────────────────────────────────────────────────────────
// App-side registration (call once after SW is active):
//
//   const reg = await navigator.serviceWorker.ready;
//   await reg.periodicSync.register("refresh-content", {
//     minInterval: 60 * 60 * 1000  // at most once per hour
//   });

self.addEventListener("periodicsync", (event) => {
  if (event.tag === "refresh-content") {
    event.waitUntil(refreshContent());
  }
});

async function refreshContent() {
  // Extend this to fetch any data you want kept fresh
  try {
    const response = await fetch("/");
    if (response.ok) {
      const cache = await caches.open(DYNAMIC_CACHE);
      await cache.put("/", response);
    }
  } catch {
    // Network unavailable — existing cache stays valid
  }
}

// ─── IndexedDB helpers (Background Sync queue) ────────────────────────────────
const DB_NAME = `${APP_NAME}-sync`;

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = (e) =>
      e.target.result.createObjectStore("pending", { keyPath: "id", autoIncrement: true });
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = () => reject(req.error);
  });
}

function getAllPending(db) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction("pending", "readonly");
    const req = tx.objectStore("pending").getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function deletePending(db, id) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction("pending", "readwrite");
    const req = tx.objectStore("pending").delete(id);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

// Exported helper so app-side code can enqueue requests
// Usage: importScripts not needed — call from app-side script using the same openDB logic
