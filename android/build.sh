#!/usr/bin/env bash
# Bubblewrap TWA build script for UwU Suite
# Run from the android/ directory: bash build.sh [apk|aab|both]
set -euo pipefail

DOMAIN="${DOMAIN:-YOUR_DOMAIN}"
OUTPUT="${1:-apk}"

check_deps() {
  for cmd in node npm java keytool; do
    command -v "$cmd" &>/dev/null || { echo "Missing: $cmd"; exit 1; }
  done
  node --version | grep -qE "v(18|20|22)" || echo "Warning: Node 18/20/22 recommended"
  java -version 2>&1 | grep -qE "version \"(11|17|21)" || echo "Warning: Java 11/17/21 recommended"
}

install_bubblewrap() {
  if ! command -v bubblewrap &>/dev/null; then
    echo "Installing @bubblewrap/cli..."
    npm install -g @bubblewrap/cli
  fi
}

patch_manifest() {
  # Replace YOUR_DOMAIN placeholder if DOMAIN env var is set
  if [ "$DOMAIN" != "YOUR_DOMAIN" ]; then
    sed -i "s/YOUR_DOMAIN/$DOMAIN/g" twa-manifest.json
    echo "Patched twa-manifest.json with domain: $DOMAIN"
  fi
}

generate_keystore() {
  if [ ! -f android.keystore ]; then
    echo "Generating signing keystore..."
    keytool -genkey -v \
      -keystore android.keystore \
      -alias uwuapps-portal \
      -keyalg RSA \
      -keysize 2048 \
      -validity 10000 \
      -dname "CN=UwU Apps, OU=UwU Apps, O=UwU Apps, L=Unknown, S=Unknown, C=US"
    echo ""
    echo "==> SHA-256 fingerprint for assetlinks.json:"
    keytool -list -v -keystore android.keystore -alias uwuapps-portal | grep "SHA256:"
    echo ""
    echo "Update .well-known/assetlinks.json with the fingerprint above, then redeploy."
  fi
}

build() {
  cp twa-manifest.json ../  # bubblewrap reads from project root
  cd ..

  case "$OUTPUT" in
    apk)
      echo "Building signed APK..."
      bubblewrap build --skipPwaValidation
      echo "APK: app-release-signed.apk"
      ;;
    aab)
      echo "Building App Bundle (AAB)..."
      bubblewrap build --skipPwaValidation
      echo "AAB: app-release-bundle.aab"
      ;;
    both)
      echo "Building both APK and AAB..."
      bubblewrap build --skipPwaValidation
      ;;
    *)
      echo "Usage: bash build.sh [apk|aab|both]"
      exit 1
      ;;
  esac
  mv twa-manifest.json android/
}

echo "=== UwU Suite TWA Builder ==="
check_deps
install_bubblewrap
patch_manifest
generate_keystore
build
echo "Done."
