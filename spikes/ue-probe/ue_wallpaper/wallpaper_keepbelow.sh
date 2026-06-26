#!/usr/bin/env bash
# wallpaper_keepbelow.sh — ADR-0029 §A rung 3, NO-BUILD native path.
#
# Turn the "Indigo Channel" UE 5.8 build into the live desktop WALLPAPER on KWin/Plasma 6
# WITHOUT any engine patch: launch UE -game (native Wayland, Vulkan) then force its window
# to the wallpaper slot with a KWin "keep below + no border + skip taskbar/pager/switcher"
# rule. It lands at stacking index [1] — above the Plasma desktop containment (so it covers
# the desktop icons, which we deliberately gave up), below every app window and the panel.
# This is the exact placement the layer-shell PoCs (rungs 1-2) proved on this box.
#
#   ./wallpaper_keepbelow.sh            # turn the UE wallpaper ON  ("select it")
#   pkill -f 'AgentOSBlank.uproject'    # turn it OFF
#
# An FPS cap (t.MaxFPS) keeps a 24/7 wallpaper from cooking the GPU / starving gens — this
# is the cheap end of the ADR-0029 Phase-B throttle ladder (full coexistence is future work).
#
# The CLEANER native path (UE binding the zwlr_layer_shell BACKGROUND role itself, input-less)
# is the LinuxWindow.cpp patch + libagentos_layershell.so in this dir — it needs a SOURCE
# build of UE 5.8; this Installed engine cannot compile engine patches in place.
set -u
UE="$HOME/UnrealEngine/Engine/Binaries/Linux/UnrealEditor"
PROJ="$HOME/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject"
MAP="${AGENTOS_WALLPAPER_MAP:-/Game/AgentOS/CalmWallpaper}"   # override: AGENTOS_WALLPAPER_MAP=/Game/AgentOS/PrismField
MAXFPS="${AGENTOS_WALLPAPER_FPS:-30}"   # override: AGENTOS_WALLPAPER_FPS=15 ./wallpaper_keepbelow.sh
[ -x "$UE" ]   || { echo "FATAL: missing $UE"; exit 1; }
[ -f "$PROJ" ] || { echo "FATAL: missing $PROJ"; exit 1; }

# RC (ADR-0029 §B / ADR-0030): start the loopback Remote Control server so `agentosd rc` can push
# the reactive MOOD MPC scalars into the live wallpaper. The lockdown (default-deny allowlist, console-
# exec off, loopback bind) lives in the project's DefaultRemoteControl.ini + DefaultEngine.ini and was
# VERIFIED 5/5 §B gates GO. Opt out with AGENTOS_WALLPAPER_RC=0 (no RC server → static wallpaper).
RC_ARGS=()
if [ "${AGENTOS_WALLPAPER_RC:-1}" = "1" ]; then
	RC_EXEC=", WebControl.StartServer"; RC_ARGS=(-RCWebControlEnable)
else
	RC_EXEC=""
fi
echo "[wallpaper] launching UE -game (t.MaxFPS $MAXFPS${RC_EXEC:+, RC on :30010}) ..."
# -unattended: the project's Source/*.Target.cs are at BuildSettingsVersion.V5, so a
# NON-unattended UnrealEditor -game launch pops a MODAL "Target Upgrade Required" zenity
# dialog and BLOCKS before the map ever loads — i.e. a permanently BLACK wallpaper window.
# -unattended auto-dismisses it (cursor visibility comes from BP_WallpaperPC, not this flag).
# Proper root fix = bump both Target.cs to BuildSettingsVersion.V7, but that forces a C++
# rebuild — do that deliberately, not as a side effect of shipping the wallpaper.
"$UE" "$PROJ" "$MAP" -game -unattended -windowed -ResX=3440 -ResY=1440 -nosplash -nosound \
	-ExecCmds="t.MaxFPS $MAXFPS${RC_EXEC}" "${RC_ARGS[@]}" >/tmp/agentos-ue-wallpaper.log 2>&1 &
UE_PID=$!
echo "[wallpaper] UE pid=$UE_PID; waiting for the -game window ..."

# KWin script: move ONLY the -game window to the wallpaper slot. Caption-scoped on
# "SF_VULKAN_SM5" so the UE EDITOR window (different caption) is never affected.
PLUGIN="agentos_wp_$$_$RANDOM"
TMP="${XDG_RUNTIME_DIR:-/tmp}/${PLUGIN}.js"
cat > "$TMP" <<'EOF'
const wins = (typeof workspace.windowList === "function") ? workspace.windowList() : workspace.clientList();
for (const w of wins) {
	const cls = String(w.resourceClass).toLowerCase();
	const cap = String(w.caption);
	if (cls.indexOf("unrealeditor") !== -1 && cap.indexOf("SF_VULKAN_SM5") !== -1) {
		w.keepBelow = true; w.skipTaskbar = true; w.skipPager = true;
		w.skipSwitcher = true; w.noBorder = true;
		print("AGENTOS-WP: wallpaper-mode applied to '" + w.caption + "' keepBelow=" + w.keepBelow);
	}
}
EOF

for i in $(seq 1 40); do
	if qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript "$TMP" "$PLUGIN" >/dev/null 2>&1; then
		qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.start >/dev/null 2>&1
		( sleep 1 ) & wait $!
		if journalctl --user -t kwin_wayland --since "4 seconds ago" 2>/dev/null | grep -q AGENTOS-WP; then
			qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript "$PLUGIN" >/dev/null 2>&1
			rm -f "$TMP"
			echo "[wallpaper] UE is now the desktop wallpaper (keep-below slot). Stop: pkill -f 'AgentOSBlank.uproject'"
			exit 0
		fi
		qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript "$PLUGIN" >/dev/null 2>&1
	fi
	kill -0 "$UE_PID" 2>/dev/null || { echo "[wallpaper] UE exited early — see /tmp/agentos-ue-wallpaper.log"; rm -f "$TMP"; exit 1; }
	( sleep 2 ) & wait $!
done
echo "[wallpaper] timed out waiting for the UE window."; rm -f "$TMP"; exit 1
