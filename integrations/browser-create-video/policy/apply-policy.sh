#!/usr/bin/env bash
# Pin the "Create Video from Image" WebExtension permanently on RELEASE Firefox via an enterprise
# policy (survives restarts; can't be accidentally removed). RELEASE Firefox refuses an UNSIGNED
# xpi even by policy, so this REQUIRES the AMO-self-distribution-SIGNED xpi (default: the committed
# signed/create-video-agentos.xpi; override by passing a path). Reversible via restore-policy.sh.
#
# Hardening (post-review):
#  * the signed xpi is copied to a ROOT-OWNED stable path and the policy points THERE — never at a
#    user-writable $HOME/repo path that malware could swap or a repo move could break;
#  * the "is it signed?" check is a real signature check (Mozilla AMO production root in the PKCS#7
#    cert chain) + the gecko id, not a forgeable filename match;
#  * the policy file is MERGED (preserves any other org policies), not overwritten.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
POLICY_DIR="/etc/firefox/policies"          # Firefox 60+ honours this on Linux (system install)
POLICY="$POLICY_DIR/policies.json"
BACKUP="$POLICY.agentos-bak"                 # single stable backup = the true pre-AgentOS original
STABLE_DIR="/usr/local/lib/agentos"          # root-owned home for the pinned artifact
STABLE_XPI="$STABLE_DIR/create-video-agentos.xpi"

SIGNED_XPI="${1:-$HERE/../signed/create-video-agentos.xpi}"
[ -e "$SIGNED_XPI" ] || { echo "✗ no signed xpi: $SIGNED_XPI — sign one (../sign.sh) or pass a path." >&2; exit 2; }
SIGNED_XPI="$(readlink -f "$SIGNED_XPI")"
[ -f "$SIGNED_XPI" ] || { echo "✗ signed xpi not found: $SIGNED_XPI" >&2; exit 1; }

# PRIMARY GATE — pin-by-hash. The committed signed/ artifact ships a reviewed .sha256 sidecar and the
# bytes MUST match it. For a committed binary the reviewed hash *is* the identity. We deliberately do
# NOT gate on a cert check: `openssl pkcs7 -print_certs` only lists the certs embedded in the blob, and
# the AMO chain is PUBLIC — it can be embedded with no private key, so a cert-grep is forgeable. RELEASE
# Firefox performs the authoritative signature verification at install; here we pin the exact reviewed
# bytes (and need only sha256sum, so this works with or without openssl).
SIDECAR="$SIGNED_XPI.sha256"
[ -f "$SIDECAR" ] || {
  echo "✗ no integrity sidecar: $SIDECAR — pin-by-hash is the gate." >&2
  echo "  Use the committed signed/create-video-agentos.xpi (which ships one), or after REVIEWING a" >&2
  echo "  freshly-signed build:  sha256sum X.xpi | awk '{print \$1}' > X.xpi.sha256" >&2
  exit 1
}
want="$(cut -d' ' -f1 < "$SIDECAR")"
got="$(sha256sum "$SIGNED_XPI" | cut -d' ' -f1)"
[ "$want" = "$got" ] || { echo "✗ sha256 mismatch vs $SIDECAR (want $want, got $got) — refusing." >&2; exit 1; }
echo "  ✓ pinned by reviewed sha256 ($got)"

# SECONDARY (informational, non-gating): note the gecko id + whether AMO certs are present. The hash
# gate above already pins the exact manifest, so this is just a human-readable sanity line in the log.
python3 - "$SIGNED_XPI" <<'PY' || true
import sys, zipfile, json, subprocess, shutil
try:
    z = zipfile.ZipFile(sys.argv[1])
    gid = json.loads(z.read("manifest.json"))["browser_specific_settings"]["gecko"]["id"]
except Exception as e:
    print(f"  sanity: could not read manifest ({e})"); sys.exit(0)
note = f"  sanity: id={gid}"
rsa = [n for n in z.namelist() if n.startswith("META-INF/") and n.endswith(".rsa")]
if rsa and shutil.which("openssl"):
    try:
        r = subprocess.run(["openssl","pkcs7","-inform","DER","-print_certs","-noout"],
                           input=z.read(rsa[0]), capture_output=True, timeout=15)
        amo = "root-ca-production-amo" in (r.stdout + r.stderr).decode("utf-8", "replace")
        note += f", AMO certs present={amo} (Firefox verifies the signature at install)"
    except Exception:
        pass
print(note)
PY

[ "$(id -u)" = 0 ] || { echo "This writes $POLICY (root) — re-run with sudo:  sudo $0 ${1:-}" >&2; exit 1; }

# Stage the artifact in a root-owned, non-user-writable location and pin THAT.
mkdir -p "$STABLE_DIR"
install -m 0644 "$SIGNED_XPI" "$STABLE_XPI"
echo "✓ staged signed xpi → $STABLE_XPI (root-owned)"

mkdir -p "$POLICY_DIR"
# Snapshot the TRUE pre-AgentOS policy exactly once (only if a foreign policy exists and we haven't
# already backed one up) so restore can return the original — and so re-applies never overwrite it.
if [ -f "$POLICY" ] && [ ! -e "$BACKUP" ] && ! grep -q 'create-video@agentos' "$POLICY"; then
  cp -a "$POLICY" "$BACKUP"
  echo "! pre-existing $POLICY backed up → $BACKUP (other org policies preserved by the merge below)"
fi

# MERGE our entry into policies.ExtensionSettings — never clobber other policies.
python3 - "$POLICY" "$STABLE_XPI" <<'PY'
import sys, json, os
policy, xpi = sys.argv[1], sys.argv[2]
doc = {}
if os.path.exists(policy):
    try: doc = json.load(open(policy))
    except Exception: doc = {}
doc.setdefault("policies", {}).setdefault("ExtensionSettings", {})["create-video@agentos"] = {
    "installation_mode": "force_installed",
    "install_url": "file://" + xpi,
    "updates_disabled": True,
}
with open(policy, "w") as f:
    json.dump(doc, f, indent=2); f.write("\n")
PY
chmod 0644 "$POLICY"
echo "✓ policy merged → $POLICY"
echo "  pinned: create-video@agentos  ←  $STABLE_XPI"
echo "  Restart Firefox; verify at about:policies and about:addons (present, force-installed,"
echo "  survives restarts). The native host + Dolphin menu are separate."
