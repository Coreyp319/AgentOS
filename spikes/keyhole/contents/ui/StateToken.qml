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
    // live aurora porthole inputs (the same floats + palette the wallpaper reads),
    // injected by the host so every surface samples ONE source (no per-surface drift).
    property color aurora: Qt.rgba(0.10, 0.13, 0.22, 1.0)   // legacy (unused by the porthole)
    property real  busy: 0.0
    property real  snag: 0.0
    property real  energy: 0.95
    property var   dawnPalette: null
    // EARNED bloom + breath — computed by KeyholeModel.ringIntensityFor / breathingFor.
    property real ringIntensity: 0
    property bool breathing: false
    // instrument skin (light/dark register); default dark so the harness renders alone
    property var skin: _defaultSkin
    InstrumentPalette { id: _defaultSkin }
    spacing: 8

    // Screen-reader semantics (ADR-0012 §7): the state word IS the accessible name,
    // keyed off the same `label` the eye reads, so spoken == seen. The name updates on
    // each transition (the swaync needs_you toast carries the assertive alert).
    Accessible.role: Accessible.StaticText
    Accessible.name: token.label

    // contrast-locked palette, bound to the active register so the token follows the toggle
    readonly property color fg: skin.text          // primary, AA on the active register
    readonly property color dimFg: skin.dim         // for unknown/snag
    readonly property color warmFg: skin.warmText   // reserved needs-you FOREGROUND (dual-register: copper in light, AA-safe)

    readonly property color tokenColor: {
        if (state === "unknown") return dimFg
        if (state === "snag")    return dimFg
        if (state === "needs_you") return warmFg
        return fg
    }

    // the glyph now wears the nimbus-aurora ring/halo (earned: calm at rest,
    // blooms with busy; warm only on needs_you). Shape + label stay contrast-locked.
    AuroraRing {
        diameter: 34
        glyph: token.glyph
        glyphColor: "#ECEFF6"     // always a light ink over the dawn (a11y; state = shape + mood + label)
        dawnPalette: token.dawnPalette
        busy: token.busy
        warm: token.warm
        snag: token.snag
        unknownState: token.state === "unknown"
        energy: token.energy
        bloom: token.ringIntensity
        breathing: token.breathing
        emphasized: token.state === "needs_you"
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
