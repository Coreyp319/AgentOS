#!/usr/bin/env bash
# Install the AgentOS read-only producers as --user services:
#   * nimbus-aurora-agent.service   (P1) → $XDG_RUNTIME_DIR/nimbus-aurora/agent.json   (wallpaper mood)
#   * nimbus-aurora-keyhole.service (ADR-0012) → …/keyhole.json (tray instrument)
#   * agentos-telemetry.service     (ADR-0018) → ~/.local/state/agentosd/telemetry.jsonl (coexistence history)
#   * agentos-coexist-report.timer  (ADR-0018) → daily `coexist` snapshot → ~/.local/state/agentosd/coexist-history.txt
# Reversible via restore.sh. Read-only wrt the system; the only things they write are those files.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
BIN_DEST="$HOME/.local/bin/agentosd"
UNITS=(agentos-lease.service agentos-queue.service nimbus-aurora-agent.service nimbus-aurora-keyhole.service agentos-telemetry.service nimbus-aurora-scene.service)
# Timer-triggered (install both, enable only the .timer).
REPORT_UNITS=(agentos-coexist-report.service agentos-coexist-report.timer)
# ADR-0030 reactive UE pusher: installed but NOT started here — `rc` PUTs to the UE MPC, which is inert
# until the reactive MPC + material taps are authored into the wallpaper scene (the GPU-gated Phase A step).
# Enable it AFTER authoring: `systemctl --user enable --now nimbus-aurora-rc.service`.
INSTALL_ONLY=(nimbus-aurora-rc.service)
UNIT_DIR="$HOME/.config/systemd/user"
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/agentosd"

command -v cargo >/dev/null 2>&1 || { echo "✗ cargo not found — install the Rust toolchain (https://rustup.rs) and re-run" >&2; exit 1; }
echo "building agentosd (release)…"
( cd "$REPO" && cargo build --release -p agentosd )

mkdir -p "$(dirname "$BIN_DEST")"
# Install via a temp + atomic rename: a SIGKILL (e.g. a one-click adopt hitting RuntimeMaxSec on a
# cold build) can never leave a half-written agentosd that the units would then fail to ExecStart.
install -m755 "$REPO/target/release/agentosd" "$BIN_DEST.new" && mv -f "$BIN_DEST.new" "$BIN_DEST"
mkdir -p "$STATE"   # telemetry self-creates it, but the coexist-report timer's `>>` redirect needs it to exist

mkdir -p "$UNIT_DIR"
for UNIT in "${UNITS[@]}" "${REPORT_UNITS[@]}" "${INSTALL_ONLY[@]}"; do
  install -m644 "$HERE/$UNIT" "$UNIT_DIR/$UNIT"
done

systemctl --user daemon-reload
if systemctl --user enable --now "${UNITS[@]}"; then
  echo "✓ producers installed + started (${UNITS[*]})"
  echo "  → org.agentos.Coordinator1            (D-Bus VRAM lease/coordinator + $RUNTIME/nimbus-aurora/lease.json)"
  echo "  → $RUNTIME/nimbus-aurora/agent.json   (wallpaper mood)"
  echo "  → $RUNTIME/nimbus-aurora/keyhole.json (tray instrument)"
  echo "  → $STATE/telemetry.jsonl              (coexistence history)"
else
  echo "! could not enable the user services; start them by hand:"
  echo "    systemctl --user enable --now ${UNITS[*]}"
fi
if systemctl --user enable --now agentos-coexist-report.timer 2>/dev/null; then
  echo "  → $STATE/coexist-history.txt          (daily coexist go/no-go snapshot)"
else
  echo "  ! enable the report timer by hand: systemctl --user enable --now agentos-coexist-report.timer"
fi
echo "  → $RUNTIME/nimbus-aurora/scene-params.json (reactive dark-ride mood, ADR-0030)"
echo "  • nimbus-aurora-rc.service installed but NOT started — enable it after the reactive MPC is authored"
echo "    into the UE wallpaper scene:  systemctl --user enable --now nimbus-aurora-rc.service"
