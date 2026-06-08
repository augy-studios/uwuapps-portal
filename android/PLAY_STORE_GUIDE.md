# UwU Suite — Google Play Store Build Guide
## Using Bubblewrap (TWA) for full minSdkVersion control

---

## Why Bubblewrap instead of PWABuilder?

PWABuilder's AAB output locks `minSdkVersion` to **API 23 (Android 6.0)**, which
limits installation to roughly 72 certified device models.

Bubblewrap lets you set **`minSdkVersion: 21` (Android 5.0 Lollipop)**, covering
~99.8% of active Android devices — thousands of additional device models.

---

## Prerequisites (Debian VPS — headless)

### 1. Node.js 20 LTS

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

### 2. Java JDK 17

```bash
sudo apt install -y openjdk-17-jdk
```

### 3. Android SDK command-line tools (no Android Studio needed)

```bash
# Install dependencies
sudo apt install -y wget unzip

# Download cmdline-tools
mkdir -p ~/android/cmdline-tools
cd ~/android/cmdline-tools
wget https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip commandlinetools-linux-*.zip
mv cmdline-tools latest   # sdkmanager expects this layout
rm commandlinetools-linux-*.zip

# Add to your shell profile (~/.bashrc or ~/.profile)
export ANDROID_SDK_ROOT="$HOME/android"
export PATH="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$ANDROID_SDK_ROOT/platform-tools:$PATH"
source ~/.bashrc

# Accept licences and install build tools
yes | sdkmanager --licenses
sdkmanager "build-tools;34.0.0" "platforms;android-34" "platform-tools"
```

### 4. Bubblewrap CLI

```bash
npm install -g @bubblewrap/cli
```

---

## Step 1 — Verify domain in twa-manifest.json

`android/twa-manifest.json` is pre-configured for `uwuapps.org` (package ID `org.uwuapps.portal`).

To build for a different domain, set the `DOMAIN` env var and the build script will patch the manifest:

```bash
DOMAIN=portal.uwuapps.org bash android/build.sh apk
```

---

## Step 2 — First-time Bubblewrap setup

```bash
# Point Bubblewrap at your SDK (run once — answer the prompts or pass flags)
bubblewrap doctor
# JAVA_HOME  →  /usr/lib/jvm/java-17-openjdk-amd64
# ANDROID_SDK_ROOT  →  /home/<you>/android
```

---

## Step 3 — Generate your signing keystore (do this ONCE, keep it safe)

```bash
keytool -genkey -v \
  -keystore android/android.keystore \
  -alias uwuapps-portal \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -dname "CN=UwU Apps, OU=UwU Apps, O=UwU Apps, L=Unknown, S=Unknown, C=US"
```

Then get your SHA-256 fingerprint:

```bash
keytool -list -v \
  -keystore android/android.keystore \
  -alias uwuapps-portal | grep "SHA256:"
```

**Update `.well-known/assetlinks.json`** with this fingerprint and redeploy your
web app before building. The TWA will fail to verify otherwise.

---

## Step 4 — Build options

### Build a signed APK (sideload / direct upload to Play Console)

```bash
cd android
bubblewrap build --skipPwaValidation
# Output: app-release-signed.apk
```

### Build an App Bundle (recommended for Play Store)

Same command — Bubblewrap outputs both `app-release-signed.apk` and
`app-release-bundle.aab` by default.

### Key build flags

| Flag | Purpose |
|------|---------|
| `--skipPwaValidation` | Skip Lighthouse PWA audit (useful if behind auth) |
| `--manifest android/twa-manifest.json` | Use a custom manifest path |

---

## Step 5 — minSdkVersion explained

In `twa-manifest.json`:

```json
"minSdkVersion": 21,   // Android 5.0 — ~99.8% device coverage
"targetSdkVersion": 34 // Android 14 — required by Google Play since Aug 2024
```

**Android API coverage table:**

| minSdkVersion | Android | Device coverage |
|---------------|---------|-----------------|
| 21 | 5.0 Lollipop | ~99.8% |
| 23 | 6.0 Marshmallow | ~97% (PWABuilder default — misses 72+ device models issue) |
| 26 | 8.0 Oreo | ~90% |

**TWA requires Chrome 72+, which runs on Android 5.0+**, so `minSdkVersion: 21`
is the safe floor — no functionality is lost by going lower.

---

## Step 6 — Service Worker features (all ticked)

Your `sw.js` now implements all six PWABuilder categories:

| Feature | Status | Implementation |
|---------|--------|----------------|
| Has Service Worker | ✅ | `sw.js` registered in `index.html` |
| Has Logic | ✅ | Cache strategies + IndexedDB sync queue |
| Offline Support | ✅ | Static + dynamic caches, `offline.html` fallback |
| Push Notifications | ✅ | `push` + `notificationclick` listeners |
| Background Sync | ✅ | `sync` listener + IndexedDB pending queue |
| Periodic Sync | ✅ | `periodicsync` listener refreshes cache hourly |

To register Periodic Sync in your app JS:

```js
const reg = await navigator.serviceWorker.ready;
await reg.periodicSync.register("refresh-content", { minInterval: 60 * 60 * 1000 });
```

To register Push Notifications in your app JS:

```js
const reg = await navigator.serviceWorker.ready;
const sub = await reg.pushManager.subscribe({
  userVisibleOnly: true,
  applicationServerKey: YOUR_VAPID_PUBLIC_KEY
});
// Send `sub` to your server to store
```

To queue a Background Sync action in your app JS:

```js
// Queue the failed request in IndexedDB first, then:
const reg = await navigator.serviceWorker.ready;
await reg.sync.register("sync-pending-actions");
```

---

## Step 7 — Upload to Google Play Console

1. Go to https://play.google.com/console → Create app
2. Fill in app details (name, description, category)
3. **Internal testing** → Create new release → Upload `app-release-bundle.aab`
4. Complete the content rating questionnaire
5. Set up store listing (screenshots already defined in `manifest.json`)
6. Publish to Internal Testing first, then promote to Production

### Required Play Console assets

| Asset | Size |
|-------|------|
| App icon | 512×512 PNG (use `UUS-512.png`) |
| Feature graphic | 1024×500 PNG |
| Phone screenshots | min 2, max 8 (you have 1 defined — add more) |
| Tablet screenshots | optional but recommended |

---

## Step 8 — Play App Signing (recommended)

When uploading your first AAB, opt into **Play App Signing**:
- Google re-signs your app with their key for distribution
- You upload with your upload key (the `android.keystore` above)
- Get the Google-managed SHA-256 from Play Console → Setup → App signing
- Add **both** SHA-256 fingerprints to `assetlinks.json`:

```json
[{
  "relation": ["delegate_permission/common.handle_all_urls"],
  "target": {
    "namespace": "android_app",
    "package_name": "org.uwuapps.portal",
    "sha256_cert_fingerprints": [
      "YOUR_UPLOAD_KEY_SHA256",
      "GOOGLE_PLAY_SIGNING_SHA256"
    ]
  }
}]
```

---

## File structure after setup

```
uwuapps-portal/
├── sw.js                          ← upgraded (all 6 features)
├── offline.html                   ← offline fallback page
├── manifest.json                  ← PWA manifest
├── .well-known/assetlinks.json    ← update SHA-256 after keygen
└── android/
    ├── twa-manifest.json          ← Bubblewrap config (domain: uwuapps.org)
    ├── build.sh                   ← build helper
    ├── android.keystore           ← generated by build.sh (DO NOT COMMIT)
    └── PLAY_STORE_GUIDE.md        ← this file
```

> **Security:** Add `android/android.keystore` and `android/*.keystore` to
> `.gitignore`. Never commit keystore files.
