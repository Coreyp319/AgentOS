// SPIKE harness — render the windable amber grass field at a FIXED iTime, with the
// wind/agent signal taken from wind_data.js (written per-run by run.sh). Fixed iTime
// makes every capture differ ONLY by the signal uniforms, so a redirect of windDir is
// the *only* thing changing between two frames (the idle invariant is then diffable).
//
// Bare QtQuick so it runs under a plain `qml6` on the live session.
// (Offscreen has no GL context here -> blank frames; see hills-reactive/README.)
import QtQuick
import "wind_data.js" as Sig

Window {
    id: win
    visible: true
    width: 1024; height: 576
    color: "black"
    title: "windable-grass spike"

    // --- dreamTex source (ADR-0023 P2.10) -------------------------------------------------
    // The REAL dream-as-texture input is the Blender EEVEE graded frame from the Phase-0
    // pipeline (spikes/creative-env/out/graded/g_0001.png, read-only). If it is present we
    // sample it; if absent we fall back to the on-palette GRADIENT stand-in (so the harness
    // still compiles + the seam is provable without creative-env). The CORE captures keep
    // uDreamMix=0 (procedural look), so the dreamTex source only matters for the demo frame.
    Image {
        id: dreamReal
        source: "../creative-env/out/graded/g_0001.png"
        visible: false
        // 640x360 EEVEE frame; fillMode keeps it framed when sampled at uv 0..1.
        fillMode: Image.Stretch
        cache: true
        asynchronous: false
    }
    // gradient fallback — only sourced into the shader if the real frame failed to load.
    Rectangle {
        id: dreamStandin
        width: 256; height: 256; visible: false
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#f4e3a1" }
            GradientStop { position: 0.6; color: "#e3c46a" }
            GradientStop { position: 1.0; color: "#7d5e22" }
        }
    }
    // whichever resolved: the real EEVEE frame if Ready, else the gradient stand-in.
    property bool dreamRealOk: dreamReal.status === Image.Ready
    ShaderEffectSource {
        id: dreamSrc
        sourceItem: win.dreamRealOk ? dreamReal : dreamStandin
        live: false; hideSource: true
        textureSize: win.dreamRealOk ? Qt.size(640, 360) : Qt.size(256, 256)
    }

    ShaderEffect {
        id: fx
        anchors.fill: parent
        fragmentShader: "windable_grass.frag.qsb"
        // uniforms map by NAME to the shader's std140 block; unspecified -> 0.
        property real     iTime: 8.0                          // fixed -> deterministic
        property vector2d iResolution: Qt.vector2d(win.width, win.height)
        // --- the live wind signal ---
        property vector2d windDir: Qt.vector2d(Sig.windX, Sig.windY)
        property real     gust:    Sig.gust
        property real     uWindReact: Sig.windReact
        // --- agent grammar (shared contract) ---
        property int      uAgentState: Sig.state
        property real     uAgentBusy:  Sig.busy
        property real     uAgentWarm:  Sig.warm
        property real     uAgentSnag:  Sig.snag
        // --- dream-as-texture ---
        property real     uDreamMix:   Sig.dreamMix
        property variant  dreamTex:    dreamSrc
        // --- a11y / liveness (ADR-0023 P2.12) ---
        property real     uReducedMotion: Sig.reducedMotion
        property real     uStale:         Sig.stale
    }

    Component.onCompleted:
        console.log("SIG windDir=(" + Sig.windX + "," + Sig.windY + ") gust=" + Sig.gust
            + " windReact=" + Sig.windReact + " dreamMix=" + Sig.dreamMix
            + " reducedMotion=" + Sig.reducedMotion + " stale=" + Sig.stale
            + " | busy=" + Sig.busy + " warm=" + Sig.warm + " snag=" + Sig.snag
            + " | dreamReal=" + (win.dreamRealOk ? "EEVEE g_0001.png" : "GRADIENT-FALLBACK"))

    Timer {
        interval: 700; running: true; repeat: false
        onTriggered: fx.grabToImage(function (res) {
            res.saveToFile("out.png");
            Qt.callLater(Qt.quit);
        })
    }
}
