// SPIKE — LIVE interactive viewer for the windable amber grass field.
//
//   qml6 spikes/windable-grass/live.qml
//
// The field animates continuously (iTime advances), and DRAGGING THE MOUSE steers the
// wind: drag direction -> wind direction, drag speed -> gust. This is a faithful stand-in
// for the real KWin window-drag producer (design-0023-window-drag-wind-producer), which is
// specced but not yet wired. It demonstrates three things the council asked to SEE, not
// just assert:
//   1. the binding  — "drag left, the field bows left" (the embodiment wedge), live;
//   2. the calm low-pass — the consumer-side spring (omega ~= 1.5 rad/s) eases the wind in
//      and back so it never snaps (this spring lives in the CONSUMER, not feed.rs);
//   3. the neutral-vector fix — windDir passed to the shader is (dir * gust), so at rest
//      gust->0 => windDir->(0,0), the shader's exact idle invariant.
//
// Bare QtQuick so it runs under a plain `qml6` on the live session.
import QtQuick

Window {
    id: win
    visible: true
    width: 1024; height: 576
    color: "black"
    title: "amber field — drag to steer the wind"

    // ---- eased wind state (the consumer-side spring) ----
    property real curDirX: 0.0
    property real curDirY: 0.0
    property real curGust: 0.0
    property real tgtDirX: 0.0
    property real tgtDirY: -1.0   // a resting bias direction; magnitude is gust, which idles to 0
    property real tgtGust: 0.0
    property real tPrev: 0.0
    property real mPrevX: 0.0
    property real mPrevY: 0.0
    // a11y: prefers-reduced-motion (toggle with R). Damps gust/ripple/parallax in-shader.
    property bool reducedMotion: false

    // procedural amber "dream" stand-in so the dreamTex sampler binds to a real texture
    // (in the real consumer this is the Blender EEVEE render; here uDreamMix=0 anyway).
    Rectangle {
        id: dreamStandin
        width: 256; height: 256; visible: false
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#f4e3a1" }
            GradientStop { position: 0.6; color: "#e3c46a" }
            GradientStop { position: 1.0; color: "#7d5e22" }
        }
    }
    ShaderEffectSource {
        id: dreamSrc; sourceItem: dreamStandin; live: false; hideSource: true
        textureSize: Qt.size(256, 256)
    }

    ShaderEffect {
        id: fx
        anchors.fill: parent
        fragmentShader: "windable_grass.frag.qsb"
        property real     iTime: 0.0
        property vector2d iResolution: Qt.vector2d(win.width, win.height)
        // windDir = eased direction * eased gust  => rest is exactly (0,0)
        property vector2d windDir: Qt.vector2d(win.curDirX * win.curGust, win.curDirY * win.curGust)
        property real     gust:    win.curGust
        property real     uWindReact: 1.0
        property int      uAgentState: 0
        property real     uAgentBusy:  0.0
        property real     uAgentWarm:  0.0
        property real     uAgentSnag:  0.0
        property real     uDreamMix:   0.0
        property variant  dreamTex:    dreamSrc
        // a11y / liveness (ADR-0023 P2.12). Live viewer has a present producer, so stale=0;
        // reduced-motion is user-togglable (R) to demonstrate the motion-sensitivity damp.
        property real     uReducedMotion: win.reducedMotion ? 1.0 : 0.0
        property real     uStale:         0.0
    }

    // ~60 Hz tick: advance time + ease wind toward target (omega ~= 1.5 rad/s).
    Timer {
        interval: 16; running: true; repeat: true
        onTriggered: {
            var dt = 0.016;
            fx.iTime += dt;
            var a = 1.0 - Math.exp(-1.5 * dt);     // consumer-side spring factor
            win.curDirX += (win.tgtDirX - win.curDirX) * a;
            win.curDirY += (win.tgtDirY - win.curDirY) * a;
            win.curGust += (win.tgtGust - win.curGust) * a;
            win.tgtGust *= 0.94;                    // a gust decays unless the drag re-feeds it
            readout.text = "wind (" + win.curDirX.toFixed(2) + ", " + win.curDirY.toFixed(2)
                + ")   gust " + win.curGust.toFixed(2)
                + (win.curGust < 0.02 ? "   · calm (idle)" : "");
        }
    }

    MouseArea {
        anchors.fill: parent
        onPressed: (m) => { win.mPrevX = m.x; win.mPrevY = m.y; }
        onReleased: { win.tgtGust = 0.0; }         // let go -> eases back to calm
        onPositionChanged: (m) => {
            var dx = m.x - win.mPrevX;
            var dy = m.y - win.mPrevY;
            win.mPrevX = m.x; win.mPrevY = m.y;
            var sp = Math.sqrt(dx*dx + dy*dy);
            if (sp > 0.5) {
                win.tgtDirX = dx / sp;
                win.tgtDirY = -dy / sp;            // screen-y is down; wind-y up = positive
                win.tgtGust = Math.min(1.0, sp * 0.06);
            }
        }
    }

    Text {
        id: readout
        anchors { left: parent.left; bottom: parent.bottom; margins: 12 }
        color: "#1c2b14"; font.pixelSize: 14; font.family: "monospace"
        text: "wind (0.00, 0.00)   gust 0.00   · calm (idle)"
    }
    Text {
        anchors { left: parent.left; top: parent.top; margins: 12 }
        color: "#2a3a1c"; font.pixelSize: 14
        text: "drag to steer the wind · release to calm · R: "
              + (win.reducedMotion ? "reduced-motion ON" : "reduced-motion off")
              + " · ⌘Q / Ctrl+Q to quit"
    }

    // R toggles prefers-reduced-motion (ADR-0023 P2.12): damps gust/ripple/parallax.
    Item {
        anchors.fill: parent
        focus: true
        Keys.onPressed: (e) => { if (e.key === Qt.Key_R) win.reducedMotion = !win.reducedMotion; }
    }
}
