/*
 * AuroraRing.qml — the keyhole glyph wearing the nimbus-aurora halo.
 *
 * The state SHAPE glyph sits inside a thin aurora ring + a soft dawn-gradient
 * halo sampled from the SAME live colour the HorizonStrip uses (KeyholeModel.
 * horizonColor) — so the glyph, the 2px strip and the VRAM gauge all breathe one
 * aurora. EARNED, per ADR-0012's "quiet-dark, density-grows-with-load": the ring
 * and halo are invisible at rest (intensity 0) and bloom/brighten with `intensity`
 * (GPU busy), warming only on needs_you (the model folds the reserved warm into
 * horizonColor). Pure QtQuick — no shader, no Canvas (ADR-0012 §7: the instrument
 * must never compete with inference for VRAM): a translucent gradient disc + a
 * Rectangle-border ring + slow tweens. The contrast-locked glyph rides on top, so
 * the halo never trades away AA legibility.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick

Item {
    id: ring
    property string glyph: "—"
    property color  glyphColor: "#E6E9F0"
    property real   glyphSize: 18
    property color  aurora: Qt.rgba(0.10, 0.13, 0.22, 1.0)  // live horizonColor
    property real   intensity: 0.0     // 0 = calm/at rest; blooms with busy; 1 on needs_you
    property bool   breathing: false   // working / needs_you / acting liveness
    property bool   reducedMotion: false
    property real   ringRatio: 1.55    // ring ⌀ as a multiple of glyphSize
    property real   haloRatio: 1.74    // halo ⌀ (and the item's reserved footprint)

    implicitWidth:  glyphSize * haloRatio
    implicitHeight: glyphSize * haloRatio

    // Eased intensity + colour so a bloom is a sunrise, not a snap (reduced-motion = instant).
    property real _i: intensity
    Behavior on _i     { enabled: !ring.reducedMotion; NumberAnimation { duration: 1400; easing.type: Easing.OutCubic } }
    Behavior on aurora { enabled: !ring.reducedMotion; ColorAnimation  { duration: 2500; easing.type: Easing.OutCubic } }

    // One breath driver (0.62‥1.0) so the layers' opacity stays a BINDING — never an
    // animation-on-property that would clobber it. Rests at 1.0 when not breathing.
    property real _breath: 1.0
    onBreathingChanged: if (!breathing) _breath = 1.0
    SequentialAnimation on _breath {
        running: ring.breathing && !ring.reducedMotion
        loops: Animation.Infinite
        // 3.2s breath, deliberately NOT synced to the 2s poll.
        NumberAnimation { to: 0.62; duration: 1600; easing.type: Easing.InOutSine }
        NumberAnimation { to: 1.00; duration: 1600; easing.type: Easing.InOutSine }
    }

    // Soft halo: a translucent vertical aurora gradient (lit high → deep low) — the
    // horizon bent into a disc. Composites over ANY surface (tray, blurred popup,
    // harness) because it's translucent — no opaque hole-punch to assume a bg.
    Rectangle {
        anchors.centerIn: parent
        width: ring.glyphSize * ring.haloRatio
        height: width
        radius: width / 2
        color: "transparent"
        visible: ring._i > 0.01
        opacity: ring._i * 0.32 * (0.72 + 0.28 * ring._breath)
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.lighter(ring.aurora, 1.55) }
            GradientStop { position: 1.0; color: Qt.darker(ring.aurora, 1.35) }
        }
    }

    // Crisp aurora ring (transparency-safe: a border stroke, transparent fill).
    Rectangle {
        anchors.centerIn: parent
        width: ring.glyphSize * ring.ringRatio
        height: width
        radius: width / 2
        color: "transparent"
        border.width: Math.max(1, Math.round(ring.glyphSize / 13))
        border.color: Qt.lighter(ring.aurora, 1.3)
        visible: ring._i > 0.01
        opacity: ring._i * ring._breath
    }

    // The state SHAPE glyph — contrast-locked, on top. Keeps a gentle breath of its
    // own when live, so the token has a pulse even before the ring blooms.
    Text {
        anchors.centerIn: parent
        text: ring.glyph
        color: ring.glyphColor
        font.pixelSize: ring.glyphSize
        font.bold: ring.intensity >= 0.99   // needs_you weight
        opacity: ring.breathing ? (0.7 + 0.3 * ring._breath) : 1.0
    }
}
