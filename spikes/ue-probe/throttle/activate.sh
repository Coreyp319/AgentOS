#!/usr/bin/env bash
# activate.sh — ADR-0029 throttle-to-coexist ON-BOX ACTIVATION (the B5 [VERIFY-LIVE] sequence).
#
#   ! bash ~/Documents/AgentOS/spikes/ue-probe/throttle/activate.sh
#
# Authored because the agent's Bash tooling was temporarily unavailable; this is the exact sequence the
# agent would run. Phased; STOPS on a hard failure (the UE compile). Every engine change is reversible.
#
# What it does, in order:
#   1. Repair the engine build env: disable the Xbox-GDK plugins that error on Linux (GRDK undefined),
#      which is what blocks the UE5Rules.dll rules-assembly from regenerating. REVERSIBLE (moved aside).
#   2. Compile the UE throttle module (UAgentOSThrottleLibrary::ApplyRung) into the editor build.
#   3. Deploy the lease daemon (B4, gated-inactive) + verify the RegisterWallpaper verb exists.
#   4. Deploy the B1 launcher (nimbus-ue-wallpaper with Register/Unregister).
#   5. Restart the UE wallpaper so it loads ApplyRung AND registers as a throttleable wallpaper.
#   6. Verify ApplyRung over Remote Control + MEASURE UE's full/floor VRAM delta, and print the
#      AGENTOS_UE_FULL/FLOOR_MIB to set so the throttle clears MIN_THROTTLE_GAIN (512 MiB) and fires.
set -uo pipefail
REPO="$HOME/Documents/AgentOS"
UE="$HOME/UnrealEngine"
PROJ="$HOME/UnrealProjects/AgentOSBlank"
BK="$UE/_agentos_disabled_gdk_plugins"
COORD=(busctl --user call org.agentos.Coordinator1 /org/agentos/Coordinator1 org.agentos.Coordinator1)
free_mib() { nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | tr -d ' '; }
rc_rung() { curl -s -m 3 -X PUT http://127.0.0.1:30010/remote/object/call -H 'Content-Type: application/json' \
  -d "{\"objectPath\":\"/Script/AgentOSBlank.Default__AgentOSThrottleLibrary\",\"functionName\":\"ApplyRung\",\"parameters\":{\"Rung\":$1}}"; }

echo "════ PHASE 1 — repair engine build env (disable Xbox-GDK plugins; reversible) ════"
mkdir -p "$BK"
for p in PlayFabParty OnlineServicesXbl OnlineSubsystemGDK; do
  s="$UE/Engine/Plugins/Online/Microsoft/$p"
  [ -d "$s" ] && mv "$s" "$BK/" && echo "  moved $p aside"
done
echo "  (RESTORE later with: mv $BK/* $UE/Engine/Plugins/Online/Microsoft/ )"

echo "════ PHASE 2 — compile the UE throttle module (editor target) ════"
"$UE/Engine/Build/BatchFiles/Linux/Build.sh" AgentOSBlankEditor Linux Development \
  -project="$PROJ/AgentOSBlank.uproject" 2>&1 | tee /tmp/ue_throttle_build.log \
  | grep -iE ": error|GRDK|RulesError|Result:|Build succeeded|Total exec" | tail -25
if grep -qiE "Result: Failed|: error|RulesError" /tmp/ue_throttle_build.log && \
   ! grep -qiE "Result: Succeeded|Build succeeded|Target is up to date" /tmp/ue_throttle_build.log; then
  echo "‼ COMPILE FAILED — see /tmp/ue_throttle_build.log."
  echo "  If MORE GRDK/Xbox plugins errored, add their names to PHASE 1's loop and re-run this script."
  exit 1
fi
echo "  ✓ throttle module compiled into the editor build."

echo "════ PHASE 3 — deploy the lease daemon (B4, gated-inactive) ════"
[ -x "$REPO/target/release/agentosd" ] || { echo "‼ no release binary — run: cargo build --release -p agentosd"; exit 1; }
cp "$REPO/target/release/agentosd" "$HOME/.local/bin/agentosd" && echo "  copied agentosd → ~/.local/bin"
systemctl --user restart agentos-lease.service; sleep 2
echo "  agentos-lease: $(systemctl --user is-active agentos-lease.service)"
if "${COORD[@]/call/introspect}" 2>/dev/null | grep -qi RegisterWallpaper; then
  echo "  ✓ RegisterWallpaper verb present"
else
  busctl --user introspect org.agentos.Coordinator1 /org/agentos/Coordinator1 2>/dev/null \
    | grep -i Wallpaper && echo "  ✓ wallpaper verbs present" || echo "  ⚠ wallpaper verbs not found — check the daemon build"
fi

echo "════ PHASE 4 — deploy the B1 launcher (Register/Unregister) ════"
cp "$REPO/spikes/ue-probe/ue_wallpaper/nimbus-ue-wallpaper" "$HOME/.local/bin/nimbus-ue-wallpaper" && echo "  copied nimbus-ue-wallpaper → ~/.local/bin"

echo "════ PHASE 5 — restart the UE wallpaper (load ApplyRung + register) ════"
"$HOME/.local/bin/nimbus-ue-wallpaper" --stop 2>/dev/null; sleep 2
"$HOME/.local/bin/nimbus-ue-wallpaper"   # self-detaches + starts the register watchdog
echo "  waiting for UE + its RC server (up to ~60s)..."
ok=0; for i in $(seq 1 60); do sleep 1; if curl -s -m 2 http://127.0.0.1:30010/remote/info >/dev/null 2>&1; then ok=1; break; fi; done
[ "$ok" = 1 ] && echo "  ✓ UE RC server answering on :30010" || echo "  ⚠ UE RC not up yet (give it longer, then re-check)"
sleep 3
echo "  registration state:"; "${COORD[@]}" RegisterWallpaper utt 0 0 0 >/dev/null 2>&1; \
  busctl --user introspect org.agentos.Coordinator1 /org/agentos/Coordinator1 >/dev/null 2>&1; \
  echo "  (the launcher's watchdog registers UE's real pid automatically)"

echo "════ PHASE 6 — verify ApplyRung + MEASURE UE full↔floor VRAM delta ════"
rc_rung 0 >/dev/null; sleep 4; FULL=$(free_mib); echo "  free, UE at FULL : ${FULL} MiB"
rc_rung 2 >/dev/null; echo "  ApplyRung(2)=Floor sent; letting UE shed (~8s)…"; sleep 8; FLOOR=$(free_mib); echo "  free, UE at FLOOR: ${FLOOR} MiB"
GAIN=$(( FLOOR - FULL )); echo "  → throttle frees ≈ ${GAIN} MiB"
rc_rung 0 >/dev/null; echo "  ApplyRung(0)=Full sent (restore)."
echo
if [ "$GAIN" -ge 512 ]; then
  echo "  ✓ ApplyRung works AND the gain (${GAIN}M) clears MIN_THROTTLE_GAIN (512M) — the throttle will FIRE."
  echo "    Set the registration footprint so yield_decision sees this gain. Edit the live launcher's env"
  echo "    (or export before launch): AGENTOS_UE_FLOOR_MIB=1000  AGENTOS_UE_FULL_MIB=$(( 1000 + GAIN ))"
  echo "    then: nimbus-ue-wallpaper --stop && nimbus-ue-wallpaper   # re-register with the real numbers"
else
  echo "  ⚠ The full→floor gain (${GAIN}M) is BELOW MIN_THROTTLE_GAIN (512M) — on this scene the throttle"
  echo "    lever is GPU-time, not VRAM, so it will stay INERT (by design). Either enrich the tableau, or"
  echo "    lower AGENTOSD_MIN_THROTTLE_GAIN_MIB on the daemon if a smaller VRAM yield is still worth it."
fi
echo "════ ACTIVATION COMPLETE — drive a knife-edge dream to confirm throttle-then-admit in the journal ════"
echo "  watch:  journalctl --user -u agentos-lease.service -f | grep -i 'ADR-0029'"
