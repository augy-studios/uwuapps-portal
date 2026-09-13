// Service worker registration and the update bar. The single place the
// worker is registered for the whole site: the bar needs the
// ServiceWorkerRegistration object, and an inline register() in the markup
// has nowhere to hand it to. See update-bar-spec.md at the repo root.
//
// A new worker never activates on its own. It downloads, installs, and waits.
// The only thing that promotes it is a person pressing Reload here.

const SW_URL = "/sw.js";

let registration = null;
let waitingWorker = null;
let reloading = false;
let dismissed = false;

function watchForUpdate() {
  if (!registration) return;

  // A worker already waiting when the page opened. This is the ordinary case
  // on the second page view after a deploy, and without it the prompt would
  // only ever reach somebody who happened to have the page open at the moment
  // the new worker finished installing.
  if (registration.waiting && navigator.serviceWorker.controller) {
    waitingWorker = registration.waiting;
    render();
  }

  registration.addEventListener("updatefound", () => {
    const installing = registration.installing;
    if (!installing) return;

    installing.addEventListener("statechange", () => {
      // `installed` with a controller present means an update. `installed`
      // with no controller is a first install, which has nothing to prompt
      // about: there is no previous version on screen to protect.
      if (installing.state === "installed" && navigator.serviceWorker.controller) {
        waitingWorker = registration.waiting ?? installing;
        render();
      }
    });
  });

  // A tab open since Tuesday is the exact reader this is for, and they come
  // back to it by switching to the tab. Ask for an update check then, rather
  // than waiting for the browser's own 24 hour one.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      registration.update().catch(() => {});
    }
  });
}

function registerWorker() {
  if (!("serviceWorker" in navigator)) return;

  navigator.serviceWorker
    .register(SW_URL)
    .then((reg) => {
      registration = reg;
      watchForUpdate();
    })
    .catch((cause) => {
      // A refused registration is not a reason to break the page. Private
      // browsing in some browsers, and any http origin that is not localhost,
      // land here.
      console.warn("service worker registration failed:", cause);
    });

  // The swap, once somebody has accepted it. Reloading here rather than in
  // the click handler is what makes the page come back on the new version:
  // the controller has changed by this point, so the reload is served by the
  // new worker and not the one being replaced.
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloading) return;
    reloading = true;
    window.location.reload();
  });
}

function render() {
  const existing = document.querySelector(".update-notice");

  if (!waitingWorker || dismissed) {
    existing?.remove();
    return;
  }

  const bar = existing ?? document.createElement("div");
  bar.className = "update-notice";
  bar.setAttribute("role", "status");
  bar.setAttribute("aria-label", "Update");
  bar.innerHTML = `
    <div class="update-notice-inner">
      <p>A new version of UwU Suite is ready.</p>
      <button type="button" class="btn btn-primary" data-sw-update>Reload</button>
      <button type="button" class="btn btn-ghost" data-sw-later>Not now</button>
    </div>
  `;

  bar.querySelector("[data-sw-update]").addEventListener("click", () => {
    // The only place anything asks for skipWaiting. The reload happens on
    // controllerchange, not here.
    waitingWorker?.postMessage("skip-waiting");
  });

  // Dismissal is for this page view only and is never stored. "Not now"
  // means not now.
  bar.querySelector("[data-sw-later]").addEventListener("click", () => {
    dismissed = true;
    render();
  });

  if (!existing) document.body.prepend(bar);
}

// Registration on `load`, not immediately: installing fetches everything the
// worker precaches, and starting that while the page is still fetching its
// own assets is how a service worker makes a first visit slower for no gain.
if (document.readyState === "complete") registerWorker();
else window.addEventListener("load", registerWorker, { once: true });
