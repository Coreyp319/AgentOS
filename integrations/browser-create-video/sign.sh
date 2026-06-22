#!/usr/bin/env bash
# Sign the Create-Video extension as an UNLISTED (self-distribution) xpi via AMO.
# Reads your AMO API creds at a HIDDEN prompt — they never touch shell history, argv (ps),
# or any file. Get them at https://addons.mozilla.org/en-US/developers/addon/api/key/
set -euo pipefail
cd "$(dirname "$0")"

command -v web-ext >/dev/null || export PATH="$HOME/.local/node-v22.22.3-linux-x64/bin:$PATH"
command -v web-ext >/dev/null || { echo "✗ web-ext not found (npm i -g web-ext)" >&2; exit 1; }

read -rp  "AMO JWT issuer (looks like user:NNNN:NN): " WEB_EXT_API_KEY
read -rsp "AMO JWT secret (input hidden): " WEB_EXT_API_SECRET; echo
export WEB_EXT_API_KEY WEB_EXT_API_SECRET

web-ext sign --channel=unlisted --source-dir=extension --artifacts-dir=dist

# Promote the freshly-signed build to the committed, stable name the installer pins.
# (dist/ is gitignored scratch; signed/create-video-agentos.xpi is the redistributable release.)
NEW="$(ls -t dist/*.xpi 2>/dev/null | head -1 || true)"
[ -n "$NEW" ] || { echo "✗ web-ext produced no xpi in dist/" >&2; exit 1; }
mkdir -p signed
cp -f "$NEW" signed/create-video-agentos.xpi

# Sanity: the signed build must embed the CURRENT source version, else stale code would ship under a
# pin that never self-updates (updates_disabled).
python3 - <<'PY'
import json, zipfile, sys
src = json.load(open("extension/manifest.json"))["version"]
got = json.loads(zipfile.ZipFile("signed/create-video-agentos.xpi").read("manifest.json"))["version"]
if src != got:
    sys.exit(f"✗ version mismatch: extension/manifest.json={src} but signed xpi={got} — re-sign")
print(f"  version check OK: {got}")
PY
# Integrity sidecar — apply-policy.sh verifies the pinned artifact against this committed hash.
sha256sum signed/create-video-agentos.xpi | awk '{print $1}' > signed/create-video-agentos.xpi.sha256
echo
echo "✓ signed → $NEW"
echo "✓ promoted → signed/create-video-agentos.xpi (+ .sha256) — commit both; they're what the installer pins"
echo "  next:  git add signed/ && sudo ./policy/apply-policy.sh"
