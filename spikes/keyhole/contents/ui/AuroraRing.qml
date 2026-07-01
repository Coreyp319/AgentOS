/*
 * AuroraRing.qml — the keyhole glyph as a PORTHOLE onto the LIVE nimbus-aurora sky.
 *
 * The porthole's background is now the REAL animated aurora: a small ShaderEffect
 * running porthole.frag.qsb — the same Prism corona the wallpaper
 * (com.nimbus.aurora's aurora.frag, uStyle 7) paints, re-paletted to the deep-navy
 * instrument register (a calm dark centre ringed by an iridescent chromatic-
 * dispersion corona). It reads the SAME live floats the wallpaper reads, so the
 * keyhole and the wallpaper move together:
 *   busy → faster flow + a touch brighter    warm → dawn glow low in frame (needs_you)
 *   snag → desaturate + dim (never red)       unknown → cold, colourless ghost
 *
 * ADR-0012 §7 amendment (2026-06-17, per user direction): this is the ONE earned
 * shader surface in the instrument. It supersedes §7's LITERAL "no shader" while
 * honouring its DEEP intent — protect VRAM for inference: the porthole is a <100px
 * ShaderEffect holding a few KB of texture, and the same shader family already runs
 * full-screen as the wallpaper, so the marginal GPU cost is rounding error. It is
 * FRAME-CAPPED (~30fps, not the display refresh), PAUSES when off screen, and
 * REDUCED-MOTION freezes it to a single still aurora frame. The horizon strip and the
 * condensed SYSTEM row stay zero-GPU; only the foveal glyph earns the live shader.
 *
 * The legibility scaffolding is unchanged (a11y): the SHAPE glyph is a light ink with a
 * dark outline over a soft scrim, so it stays AA over the brightest crest / warm bloom
 * in BOTH registers; the rim frames the disc; the earned outer halo blooms with load.
 * The circular porthole is masked IN THE SHADER (transparent corners) — so no mask item
 * and no QtQuick.Effects / Qt5Compat import is needed (dependency-light, QtQuick only).
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick

Item {
    id: ring

    // --- geometry ------------------------------------------------------------
    property real diameter: 36
    implicitWidth:  diameter + 8     // room for the earned outer bloom
    implicitHeight: diameter + 8

    // --- live mood (the floats the wallpaper also reads) ---------------------
    property real busy: 0.0
    property real warm: 0.0
    property real snag: 0.0
    property real music: 0.0         // SECONDARY beat-shimmer (host pre-gates by MusicReact)
    property bool unknownState: false
    property real energy: 0.95       // overall brightness (idle calm; working lifts — lit-from-within)
    property real bloom: 0.0         // earned outer glow strength (KeyholeModel.ringIntensityFor)
    property bool hovered: false      // dock affordance: brightens the rim on hover

    // --- palette ------------------------------------------------------------
    // `palette` is the active nimbus-aurora WALLPAPER ramp — 5 colour stops (dark→bright),
    // read by main.qml from the wallpaper config and threaded via the skin. It paints the
    // porthole corona in the shader AND the earned halo/rim crest here, so the glyph wears
    // the same theme the wallpaper does and tracks it live. Default = Big Sur (the
    // wallpaper's own default) so the standalone harness still renders.
    // (named themeStops, NOT `palette` — Item.palette already exists and shadowing it warns)
    property var themeStops: ["#0d0f29", "#1c2e73", "#4552b8", "#8f5cb8", "#fa8c73"]
    readonly property color _c0: (ring.themeStops && ring.themeStops.length === 5) ? ring.themeStops[0] : "#0d0f29"
    readonly property color _c1: (ring.themeStops && ring.themeStops.length === 5) ? ring.themeStops[1] : "#1c2e73"
    readonly property color _c2: (ring.themeStops && ring.themeStops.length === 5) ? ring.themeStops[2] : "#4552b8"
    readonly property color _c3: (ring.themeStops && ring.themeStops.length === 5) ? ring.themeStops[3] : "#8f5cb8"
    readonly property color _c4: (ring.themeStops && ring.themeStops.length === 5) ? ring.themeStops[4] : "#fa8c73"
    readonly property color _crest: ring._c4   // the bright crest tints the earned bloom/rim

    // --- a11y shape + motion -------------------------------------------------
    property string glyph: "○"
    property color  glyphColor: "#ECEFF6"   // ALWAYS a light ink (legible on any dawn); state is shape+mood
    property bool   breathing: false         // legacy hint (callers still set it); the live flow now carries motion
    property bool   reducedMotion: false
    property bool   emphasized: false

    // Eased mood so a transition is a sunrise, not a snap (reduced-motion = instant).
    // These drive the shader's mood uniforms, so the porthole eases between states too.
    property real _busy:   busy
    property real _warm:   warm
    property real _snag:   snag
    property real _energy: energy
    property real _bloom:  bloom
    // music eases on a SHORT envelope so a kick reads as a quick shimmer, not a slow swell.
    property real _music:  ring.reducedMotion ? 0.0 : music
    Behavior on _busy   { enabled: !ring.reducedMotion; NumberAnimation { duration: 1200; easing.type: Easing.OutCubic } }
    Behavior on _warm   { enabled: !ring.reducedMotion; NumberAnimation { duration: 1500; easing.type: Easing.OutCubic } }
    Behavior on _snag   { enabled: !ring.reducedMotion; NumberAnimation { duration: 1200; easing.type: Easing.OutCubic } }
    Behavior on _energy { enabled: !ring.reducedMotion; NumberAnimation { duration: 1200; easing.type: Easing.OutCubic } }
    Behavior on _bloom  { enabled: !ring.reducedMotion; NumberAnimation { duration: 1200; easing.type: Easing.OutCubic } }
    Behavior on _music  { enabled: !ring.reducedMotion; NumberAnimation { duration: 180;  easing.type: Easing.OutCubic } }

    // --- the live aurora clock -----------------------------------------------
    // iTime advances ONLY while the porthole is on screen and motion is allowed; the
    // ~30fps cap (below the display refresh) keeps the cost honest. When paused (hidden
    // or reduced-motion) it HOLDS at a representative frame, so the disc is never black —
    // it shows a single still aurora, like a reduced-motion wallpaper. busy speeds the
    // flow inside the shader, so working/idle differ by PACE as well as brightness.
    property real _shaderTime: 6.0
    readonly property bool _animate: ring.visible && !ring.reducedMotion
    Timer {
        interval: 33; running: ring._animate; repeat: true   // ~30fps frame cap
        onTriggered: ring._shaderTime += 0.033
    }

    // --- earned outer bloom (a soft aurora halo, grows with load) ------------
    Rectangle {
        anchors.centerIn: parent
        width: ring.diameter + Math.max(4, ring.diameter * 0.16); height: width; radius: width / 2
        color: "transparent"
        border.width: Math.max(2, Math.round(ring.diameter * 0.08))
        border.color: Qt.rgba(ring._crest.r, ring._crest.g, ring._crest.b, 0.34 * ring._bloom)
        visible: ring._bloom > 0.01
    }

    // --- the porthole disc: the LIVE nimbus-aurora flow ----------------------
    // A square ShaderEffect that masks itself to a circle (transparent corners), so it
    // reads as a porthole with no clip/mask item. Uniforms bind by NAME to the shader's
    // std140 block; the eased mood floats drive it, so glyph and wallpaper move together.
    ShaderEffect {
        id: sky
        anchors.centerIn: parent
        width: ring.diameter; height: width
        blending: true
        fragmentShader: "porthole.frag.qsb"
        property real     iTime: ring._shaderTime
        property vector2d iResolution: Qt.vector2d(width, height)
        property real     uBusy: ring._busy
        property real     uWarm: ring._warm
        property real     uSnag: ring._snag
        property real     uIntensity: ring._energy
        property real     uGray: ring.unknownState ? 1.0 : 0.0
        property real     uMusic: ring._music
        // the live wallpaper ramp (5 stops) → the corona wears the wallpaper's theme
        property vector4d uC0: Qt.vector4d(ring._c0.r, ring._c0.g, ring._c0.b, 1.0)
        property vector4d uC1: Qt.vector4d(ring._c1.r, ring._c1.g, ring._c1.b, 1.0)
        property vector4d uC2: Qt.vector4d(ring._c2.r, ring._c2.g, ring._c2.b, 1.0)
        property vector4d uC3: Qt.vector4d(ring._c3.r, ring._c3.g, ring._c3.b, 1.0)
        property vector4d uC4: Qt.vector4d(ring._c4.r, ring._c4.g, ring._c4.b, 1.0)
        Behavior on uGray { enabled: !ring.reducedMotion; NumberAnimation { duration: 900 } }
    }

    // --- the keyhole rim (thin cool frame; brightens on hover for affordance) -
    Rectangle {
        anchors.centerIn: parent
        width: ring.diameter; height: width; radius: width / 2
        color: "transparent"
        border.width: Math.max(1, Math.round(ring.diameter / 22))
        border.color: Qt.rgba(0.66, 0.74, 0.96,
                              ring.unknownState ? 0.30 : (ring.hovered ? 0.95 : 0.66))
        Behavior on border.color { enabled: !ring.reducedMotion; ColorAnimation { duration: 140 } }
    }

    // --- a11y SHAPE glyph: light ink + dark OUTLINE + soft scrim --------------
    // Always light, decoupled from the panel text token, so the legibility-bearing
    // shape stays AA over the bright crest / warm bloom in BOTH registers.
    Rectangle {   // soft contrast scrim right under the glyph (keeps the surrounding aurora visible)
        anchors.centerIn: parent
        width: ring.diameter * 0.52; height: width; radius: width / 2
        color: Qt.rgba(0, 0, 0, 0.22)
    }
    Text {
        anchors.centerIn: parent
        text: ring.glyph
        color: ring.glyphColor
        font.pixelSize: Math.round(ring.diameter * 0.40)
        font.bold: ring.emphasized
        style: Text.Outline
        styleColor: Qt.rgba(0, 0, 0, 0.82)
        opacity: 0.97
    }
}
