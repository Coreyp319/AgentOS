/*
 * main.qml — the keyhole plasmoid root (Plasma 6 PlasmoidItem).
 *
 * Wires the three representations and the file-poll seam:
 *   - compactRepresentation : the tray glyph (idle-vanish via Plasmoid.status)
 *   - fullRepresentation    : the arbitration-led instrument panel
 *   - KeyholeModel          : the Timer-poll of keyhole.json (NOT sync-XHR)
 *
 * The native KDE blurred popup is provided by Plasma itself: a PlasmoidItem's
 * fullRepresentation is hosted in a PlasmaCore Dialog whose background is the
 * translucent, blurred panel theme surface. We DO NOT hand-roll a shader
 * (ADR-0012: the instrument must never compete with inference for VRAM).
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Layouts
import QtCore                       // StandardPaths singleton (Qt 6.5+)

import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    // The shared state/poll model. Child reps reference it as `keyhole`.
    property var keyhole: model
    // The boot-health board model (status-panel :9123). Polled only while open.
    property var servicesModel: services
    // The instrument skin, switched by the active colour scheme so every surface
    // reacts to the desktop light/dark toggle. The dependency-light reps just consume it.
    property var skin: instSkin
    InstrumentPalette { id: instSkin; dark: root.schemeDark }

    // Light/dark follows the active COLOUR SCHEME (kdeglobals), which the taskbar toggle
    // changes via `plasma-apply-colorscheme`. We read it through the executable DataSource,
    // NOT Kirigami.Theme: a plasmoid's Kirigami.Theme reflects the Plasma *desktop theme*,
    // and the WhiteSur desktop themes ship the SAME dark colours in both light and dark
    // variants — so Kirigami.Theme never changes here. The scheme's window background does.
    property bool schemeDark: true
    // ~/.config/kdeglobals, resolved the same way feedPath resolves the runtime dir:
    // StandardPaths returns a file:// QUrl; the `cat` backend needs a plain path. Honors
    // XDG_CONFIG_HOME automatically (unlike a hardcoded $HOME/.config fallback).
    readonly property string _kdeglobals:
        StandardPaths.writableLocation(StandardPaths.GenericConfigLocation).toString()
            .replace(/^file:\/\//, "") + "/kdeglobals"
    // Read the active colour scheme's window background straight from the kdeglobals
    // INI via `cat` — NOT `kreadconfig6`. kreadconfig6 is a Qt *GUI* binary whose KConfig
    // writability probe can transiently fail (cold boot / xdg-portal stall / a non-writable
    // ~/.config/kreadconfig6rc) and then pop a BLOCKING "kreadconfig6rc not writable" modal.
    // Polled every few seconds inside plasmashell, that one bad probe becomes an endless
    // modal loop on the user's screen. Parsing the INI ourselves spawns no Qt toolkit, so
    // the modal is structurally impossible — the same reason swaync-apply-scheme.sh reads
    // the INI with awk instead of kreadconfig6. Value read is identical.
    function readScheme() {
        fileReader.read(root._kdeglobals, function(txt) {
            var lines = String(txt).split("\n")
            var inWindow = false
            for (var i = 0; i < lines.length; i++) {
                var ln = lines[i].trim()
                if (ln.length === 0) continue
                if (ln.charAt(0) === "[") { inWindow = (ln === "[Colors:Window]"); continue }
                if (!inWindow) continue
                var eq = ln.indexOf("=")
                if (eq < 0 || ln.substring(0, eq).trim() !== "BackgroundNormal") continue
                var p = ln.substring(eq + 1).trim().split(",")
                if (p.length >= 3) {
                    var r = parseInt(p[0]), g = parseInt(p[1]), b = parseInt(p[2])
                    if (!isNaN(r) && !isNaN(g) && !isNaN(b))
                        root.schemeDark = (0.299 * r + 0.587 * g + 0.114 * b) < 128
                }
                return   // found BackgroundNormal in [Colors:Window]; done
            }
        })
    }

    // --- wallpaper palette: the porthole wears the active nimbus-aurora theme ---------
    // ~/.config/plasma-org.kde.plasma.desktop-appletsrc, resolved like _kdeglobals.
    readonly property string _appletsrc:
        StandardPaths.writableLocation(StandardPaths.GenericConfigLocation).toString()
            .replace(/^file:\/\//, "") + "/plasma-org.kde.plasma.desktop-appletsrc"
    // The 5 stops (dark→bright) each wallpaper Theme applies — ported verbatim from the
    // wallpaper's config.qml themePalettes so the porthole matches what it paints. Index
    // 9 (Custom) is filled live from the config's Color0..4; 0..8 are the presets.
    readonly property var _themePalettes: [
        ["#0d0f29","#1c2e73","#4552b8","#8f5cb8","#fa8c73"], // 0 Big Sur
        ["#0a1733","#126185","#3385c7","#9e70bd","#f5a8b8"], // 1 Monterey
        ["#12141a","#292e38","#525966","#8c94a3","#d1d9e6"], // 2 Graphite
        ["#1a0d2e","#591a4d","#b83861","#f27347","#ffcc73"], // 3 Sunset
        ["#2e3340","#3b4252","#5e82ab","#87bfd1","#d9dee8"], // 4 Nord
        ["#120a1f","#29144d","#8c29b3","#f259bd","#38e6eb"], // 5 Laserwave
        ["#1f1238","#613d8f","#bd6bf2","#ff73cc","#66e0fa"], // 6 Vaporwave
        ["#05050d","#0a243d","#0094c2","#ff298f","#faeb33"], // 7 Cyberpunk
        ["#0d0529","#330f66","#d9268c","#ff6b4d","#ffdb4d"]  // 8 Outrun
    ]
    // Read the first nimbus-aurora wallpaper group from the appletsrc (same INI-cat path
    // as the colour scheme — no kreadconfig6) and hand the porthole its 5 stops. A preset
    // Theme uses the table; Theme 9 (Custom) uses the group's own Color0..4.
    function readWallpaper() {
        fileReader.read(root._appletsrc, function(txt) {
            var lines = String(txt).split("\n")
            var inGrp = false, found = false, theme = 0, mreact = 0.30
            var custom = ["#0d0f29","#1c2e73","#4552b8","#8f5cb8","#fa8c73"]  // Color0..4 defaults
            for (var i = 0; i < lines.length; i++) {
                var ln = lines[i].trim()
                if (ln.length === 0) continue
                if (ln.charAt(0) === "[") {
                    if (inGrp) break   // reached the end of the group; stop
                    inGrp = /\[Wallpaper\]\[com\.nimbus\.aurora\]\[General\]$/.test(ln)
                    if (inGrp) found = true
                    continue
                }
                if (!inGrp) continue
                var eq = ln.indexOf("="); if (eq < 0) continue
                var key = ln.substring(0, eq).trim(), val = ln.substring(eq + 1).trim()
                if (key === "Theme") { var t = parseInt(val); if (!isNaN(t)) theme = t }
                else if (key === "MusicReact") { var m = parseFloat(val); if (!isNaN(m)) mreact = m }
                else if (key.length === 6 && key.substring(0, 5) === "Color") {
                    var idx = parseInt(key.charAt(5))
                    if (!isNaN(idx) && idx >= 0 && idx <= 4) custom[idx] = val
                }
            }
            if (!found) { root.musicReact = 0.0; return }  // no aurora wallpaper → no shimmer
            instSkin.portholeStops = (theme === 9) ? custom
                : (theme >= 0 && theme < root._themePalettes.length) ? root._themePalettes[theme]
                : root._themePalettes[0]
            root.musicReact = mreact
        })
    }

    // --- music shimmer: the porthole borrows the wallpaper's audio bridge -------------
    // The SAME audio.json the wallpaper reads (pw-cat→FFT systemd service), in the runtime
    // dir beside keyhole.json. We take level+beat (NOT the 32-bin spectrum — too busy at
    // icon scale), gate by the wallpaper's MusicReact, and hand the porthole ONE pre-gated
    // shimmer scalar. Polled adaptively: brisk while sound is present, lazy when silent.
    property real musicReact: 0.30
    property bool _audioHot: false
    readonly property string _audioPath:
        StandardPaths.writableLocation(StandardPaths.RuntimeLocation).toString()
            .replace(/^file:\/\//, "") + "/nimbus-aurora/audio.json"
    function pollAudio() {
        if (model.reducedMotion || root.musicReact <= 0.0) { model.music = 0; root._audioHot = false; return }
        fileReader.read(root._audioPath, function(txt) {
            if (!txt || txt.length === 0) { model.music = 0; root._audioHot = false; return }
            var lvl = 0, beat = 0
            try { var d = JSON.parse(txt); lvl = +d.level || 0; beat = +d.beat || 0 } catch (e) { return }
            var gate = Math.min(1.0, root.musicReact * 3.0)      // 0.33+ → full; subtle below
            model.music = gate * Math.min(1.5, lvl * 0.5 + beat * 1.0)
            root._audioHot = (lvl > 0.02 || beat > 0.02)
        })
    }
    // Brisk (~7Hz) while sound is present so kicks land; lazy (~1.4Hz) when silent so the
    // tray instrument stays calm at rest. Stops entirely under reduced-motion / MusicReact 0.
    Timer {
        interval: root._audioHot ? 140 : 700
        running: root.musicReact > 0.0 && !model.reducedMotion
        repeat: true
        onTriggered: root.pollAudio()
    }
    // Re-read on a calm cadence so a toggle is picked up within a few seconds, plus
    // immediately at load and whenever the popup opens.
    Timer {
        interval: 3000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: { root.readScheme(); root.readWallpaper() }
    }

    KeyholeModel {
        id: model
        // $XDG_RUNTIME_DIR/nimbus-aurora/keyhole.json, resolved via StandardPaths
        // (RuntimeLocation == XDG_RUNTIME_DIR). No env-reading hacks, no extra dep.
        // Qt 6 QML StandardPaths.writableLocation returns a QUrl (file://…), but the
        // executable `cat` backend needs a plain filesystem path — otherwise it runs
        // `cat 'file:///run/user/1000/…'` which fails and the feed never loads (the
        // plasmoid then shows a permanent "unavailable"). Strip the scheme.
        feedPath: StandardPaths.writableLocation(StandardPaths.RuntimeLocation).toString()
                  .replace(/^file:\/\//, "") + "/nimbus-aurora/keyhole.json"
        // reduced-motion follows the Plasma animation-speed setting (clamps tweens too)
        reducedMotion: Kirigami.Units.longDuration <= 1
        // PROVEN production reader: Plasma5Support executable `cat`. qml6's XHR on
        // file:// is disabled by default and plasmashell does NOT set the override
        // env var, so XHR would read empty. DataSource reads with no flag.
        readBackend: function(path, onText) { fileReader.read(path, onText) }
    }

    // The robust shell reader (Plasma5Support executable engine). Re-runs its command
    // each tick; off the hot path of NVML / the lease lock. `read` fetches a file via
    // `cat` (the keyhole.json feed); `run` executes an arbitrary command (the services
    // board uses it for a short-timeout `curl` against the status panel). Same proven
    // engine in both cases — qml6's file:// XHR is disabled and plasmashell sets no
    // override, so DataSource is the reliable reader here.
    P5Support.DataSource {
        id: fileReader
        engine: "executable"
        connectedSources: []
        property var _cb: ({})          // source (command) -> callback
        function run(cmd, onText) {
            _cb[cmd] = onText
            connectSource(cmd)
        }
        function read(path, onText) { run("cat " + shellQuote(path), onText) }
        function shellQuote(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'" }
        onNewData: function(source, data) {
            disconnectSource(source)    // one-shot per tick; fresh read next tick
            var cb = _cb[source]; delete _cb[source]
            if (cb) cb((data && data["exit code"] === 0) ? (data["stdout"] || "") : "")
        }
    }

    // Boot-health board feed: the keyhole pulls the status panel's /status.json via
    // the SAME executable engine (curl), only while the popup is open, and degrades to
    // the honest UNAVAILABLE posture if the panel is down. No new IPC, no panel changes.
    ServicesModel {
        id: services
        active: root.expanded
        readBackend: function(onText) {
            fileReader.run("curl -s --max-time 5 " + fileReader.shellQuote(services.url), onText)
        }
    }
    // Prime the board ONCE at load so its rows (and thus the popup's measured height)
    // exist before the popup is first shown — otherwise the popup measures itself while
    // the board is still empty, locks in a short height, and clips when data arrives.
    Component.onCompleted: { services.poll(); readScheme(); readWallpaper() }
    // Reopening fetches fresh immediately (don't wait for the first interval).
    onExpandedChanged: if (root.expanded) { services.poll(); readScheme(); readWallpaper() }

    // --- Tray idle-vanish: byte-identical-to-baseline at true idle ----------
    Plasmoid.status: {
        var s = model.effectiveState
        if (s === "needs_you") return PlasmaCore.Types.NeedsAttentionStatus
        // As a standalone panel widget (not a tray item) PassiveStatus does NOT cleanly
        // vanish — it just stops the idle glyph presenting. Keep idle ACTIVE so the calm
        // ○ stays visible (CompactRepresentation dims it); the tray idle-vanish is moot here.
        // unknown is shown (dimmed) — honesty: a broken substrate must NOT look idle
        return PlasmaCore.Types.ActiveStatus
    }

    Plasmoid.icon: "view-visible"
    toolTipMainText: "AgentOS Keyhole"
    toolTipSubText: model.labelFor(model.effectiveState)

    // On the desktop show the full panel; on a panel keep the compact tray glyph.
    preferredRepresentation: Plasmoid.formFactor === PlasmaCore.Types.Planar
                             ? fullRepresentation : compactRepresentation

    compactRepresentation: CompactRepresentation { plasmoidItem: root; skin: root.skin }

    // FullRepresentation already carries implicitWidth/implicitHeight, so it can be the
    // popup root directly — matching the shipped tray-plasmoid pattern (org.kde.kdeconnect).
    // A ColumnLayout wrapper here produced a popup the system tray would not show.
    fullRepresentation: FullRepresentation {
        model: root.keyhole
        services: root.servicesModel
        skin: root.skin
    }
}
