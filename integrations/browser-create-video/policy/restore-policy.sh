#!/usr/bin/env bash
# Reverse apply-policy.sh. Merge-safe: removes ONLY our create-video entry from
# policies.ExtensionSettings (any other org policies are preserved). If that empties the file and a
# pre-apply backup exists, restore the true original; if it empties the file and we created it,
# remove it. Also removes the root-staged signed artifact. Reversible.
set -euo pipefail

POLICY_DIR="/etc/firefox/policies"
POLICY="$POLICY_DIR/policies.json"
BACKUP="$POLICY.agentos-bak"
STABLE_XPI="/usr/local/lib/agentos/create-video-agentos.xpi"

[ -f "$POLICY" ] || { echo "nothing to do — $POLICY absent."; rm -f "$STABLE_XPI" 2>/dev/null || true; exit 0; }
grep -q 'create-video@agentos' "$POLICY" || { echo "$POLICY has no create-video entry — leaving it untouched."; exit 0; }
[ "$(id -u)" = 0 ] || { echo "re-run with sudo:  sudo $0" >&2; exit 1; }

# Prune our entry. Prints EMPTY if nothing meaningful remains, KEEP otherwise.
state="$(python3 - "$POLICY" <<'PY'
import sys, json
policy = sys.argv[1]
try: doc = json.load(open(policy))
except Exception: doc = {}
es = doc.get("policies", {}).get("ExtensionSettings", {})
es.pop("create-video@agentos", None)
if not es:
    doc.get("policies", {}).pop("ExtensionSettings", None)
remaining = doc.get("policies", {})
if not remaining:
    print("EMPTY")
else:
    with open(policy, "w") as f:
        json.dump(doc, f, indent=2); f.write("\n")
    print("KEEP")
PY
)"

if [ "$state" = "KEEP" ]; then
  chmod 0644 "$POLICY"
  echo "✓ removed only the create-video pin; other policies in $POLICY preserved."
elif [ -f "$BACKUP" ]; then
  mv -f "$BACKUP" "$POLICY"
  echo "✓ restored the true pre-AgentOS policy from $BACKUP."
else
  rm -f "$POLICY"
  echo "✓ removed $POLICY (it only held the create-video pin)."
fi

rm -f "$STABLE_XPI" 2>/dev/null || true
rmdir /usr/local/lib/agentos 2>/dev/null || true
echo "✓ removed root-staged artifact → $STABLE_XPI"
echo "  Restart Firefox; the extension reverts to not-installed (host + Dolphin menu unaffected)."
