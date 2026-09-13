// Bump this on every deploy that changes anything the worker serves. The
// browser compares this file byte for byte, so if nothing here changes there
// is no update to prompt about, however much else in the site has moved.
// See update-bar-spec.md at the repo root.
const CACHE_VERSION = "uwusuite-v13";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const DYNAMIC_CACHE = `${CACHE_VERSION}-dynamic`;
const OFFLINE_URL = "/offline.html";

const STATIC_ASSETS = [
  "/",
  "/index.html",
  "/style.css",
  "/script.js",
  "/js/theme.js",
  "/js/icons.js",
  "/js/ui.js",
  "/js/sw-update.js",
  "/UUS-main.png",
  "/UUS-512.png",
  "/UUS-192.png",
  "/favicon.ico",
  "/manifest.json",
  OFFLINE_URL
];

// Install. No skipWaiting() here: a new worker downloads, installs, and then
// waits. The only thing that promotes it is a person pressing Reload on the
// update bar, which arrives as the "skip-waiting" message below.
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(STATIC_ASSETS))
  );
});

// Activate. No clients.claim() here either, for the same reason.
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
});

// The one message the page sends, once somebody has accepted the update.
self.addEventListener("message", (event) => {
  const type = typeof event.data === "string" ? event.data : event.data?.type;

  // The only place either of these is ever called.
  if (type === "skip-waiting") {
    event.waitUntil(self.skipWaiting().then(() => self.clients.claim()));
  }
});

// Fetch, Offline Support
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" || !event.request.url.startsWith("http")) return;

  // API: network-first, graceful JSON error when offline
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

  // Everything else: cache-first with network update
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const networkFetch = fetch(event.request).then((response) => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(DYNAMIC_CACHE).then((cache) => cache.put(event.request, clone));
        }
        return response;
      }).catch(() => {
        if (event.request.headers.get("Accept")?.includes("text/html")) {
          return caches.match(OFFLINE_URL);
        }
        return new Response("Offline", { status: 503 });
      });
      return cached || networkFetch;
    })
  );
});

// Push Notifications
self.addEventListener("push", (event) => {
  let data = {
    title: "UwU Suite",
    body: "You have a new notification!",
    icon: "/UUS-192.png",
    badge: "/UUS-192.png",
    tag: "uwuapps-notification"
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

// Background Sync
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
      // Will be retried on the next sync event
    }
  }
}

// Periodic Sync
self.addEventListener("periodicsync", (event) => {
  if (event.tag === "refresh-content") {
    event.waitUntil(refreshContent());
  }
});

async function refreshContent() {
  try {
    const response = await fetch("/");
    if (response.ok) {
      const cache = await caches.open(DYNAMIC_CACHE);
      await cache.put("/", response);
    }
  } catch {
    // Network unavailable, existing cache stays valid
  }
}

// IndexedDB helpers (for Background Sync queue)
function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("uwuapps-sync", 1);
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
