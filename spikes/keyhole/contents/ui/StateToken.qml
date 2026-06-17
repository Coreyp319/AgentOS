/*
 * StateToken.qml — the contrast-locked status token: SHAPE glyph + TEXT label,
 * never color-only (a11y canon). The glyph is a distinct shape per state; the
 * label is plain words; color is a third, redundant channel.
 *
 * The `snag` glyph ▢ is rendered with a dashed feel by drawing it dim; the
 * `needs_you` token is the ONE warm element (reserved dawn-glow). Everything
 * else is quiet on a contrast-locked foreground so it stays AA-legible even
 * under the snag-desaturate worst case.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Layouts

RowLayout {
    id: token
    property string glyph: "—"
    property string label: "Status unavailable"
    property string state: "unknown"
    property real warm: 0.0
    property bool reducedMotion: false
    // live aurora colour (the HorizonStrip's source) + busy level, injected by the host
    property color aurora: Qt.rgba(0.10, 0.13, 0.22, 1.0)
    property real  busy: 0.0
    // EARNED ring: calm (0) at idle/snag/unknown; blooms with busy when working;
    // full + warm on needs_you (horizonColor already carries the reserved warm).
    readonly property real ringIntensity: {
        if (state === "needs_you") return 1.0
        if (state === "working" || state === "acting") return Math.max(0.4, Math.min(1.0, busy))
        return 0.0
    }
    readonly property bool breathing: state === "working" || state === "needs_you" || state === "acting"
    // instrument skin (light/dark register); default dark so the harness renders alone
    property var skin: _defaultSkin
    InstrumentPalette { id: _defaultSkin }
    spacing: 8

    // contrast-locked palette, bound to the active register so the token follows the toggle
    readonly property color fg: skin.text          // primary, AA on the active register
    readonly property color dimFg: skin.dim         // for unknown/snag
    readonly property color warmFg: skin.warm       // the reserved dawn-glow rgb(255,153,87)

    readonly property color tokenColor: {
        if (state === "unknown") return dimFg
        if (state === "snag")    return dimFg
        if (state === "needs_you") return warmFg
        return fg
    }

    // the glyph now wears the nimbus-aurora ring/halo (earned: calm at rest,
    // blooms with busy; warm only on needs_you). Shape + label stay contrast-locked.
    AuroraRing {
        glyph: token.glyph
        glyphColor: token.tokenColor
        glyphSize: 18
        aurora: token.aurora
        intensity: token.ringIntensity
        breathing: token.breathing
        reducedMotion: token.reducedMotion
        Layout.alignment: Qt.AlignVCenter
    }

    Text {
        text: token.label
        color: token.tokenColor
        font.pixelSize: 14
        font.bold: token.state === "needs_you"
        elide: Text.ElideRight
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignVCenter
    }
}
