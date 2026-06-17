/*
 * InstrumentPalette.qml — the keyhole's two-register "glass" skin.
 *
 * One source for both the canonical deep-navy instrument register (the values in
 * integrations/design/instrument-tokens.md) and a light frosted register, switched
 * by `dark`. Dependency-light (QtQuick only) ON PURPOSE so the harness and the
 * shared representations (FullRepresentation / StateToken) consume it WITHOUT
 * importing Kirigami. The plasmoid host (main.qml) derives `dark` from
 * Kirigami.Theme and injects it, so every surface reacts to the desktop's
 * light/dark toggle. Accents that must stay legible in both registers are tuned
 * per-register; the reserved warm "needs-you" hue is held identical across both.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick

QtObject {
    // The host sets this from the active colour scheme; default dark = the
    // canonical instrument register (also what the standalone harness shows).
    property bool dark: true

    // --- chrome ---------------------------------------------------------------
    readonly property color base:      dark ? "#12141C" : "#F1F3F8"   // panel base
    readonly property color deep:      dark ? "#161A28" : "#E6EAF3"   // mid background
    readonly property color text:      dark ? "#E6E9F0" : "#1A1F2C"   // primary text
    readonly property color muted:     dark ? "#B4BAC8" : "#3C4356"   // secondary text
    readonly property color label:     dark ? "#7A8090" : "#5A6173"   // quiet labels (≥4.5:1 on register)
    readonly property color dim:       dark ? "#8A90A0" : "#6B7184"   // unknown / snag / idle
    readonly property color hairline:  dark ? "#262A36" : "#CFD5E2"   // 1px separators
    readonly property color tintHover: dark ? "#1C2230" : "#DDE2EE"   // row hover

    // --- accents --------------------------------------------------------------
    readonly property color blue:      dark ? "#7AA2FF" : "#2C57C9"   // link / acting (contrast-tuned)
    readonly property color warm:      "#FF9957"                       // RESERVED needs-you cue (both registers)

    // --- status ramp (small marks; darkened in the light register) ------------
    readonly property color stUp:      dark ? "#86B89A" : "#2C7A50"   // healthy
    readonly property color stAmber:   dark ? "#D9B45A" : "#8A6310"   // transitional

    // --- aurora ramp (cool dawn: the VRAM gauge fill + the glyph-ring halo) ----
    // The cool half of the nimbus-aurora dawn (indigo → blue → violet). Spent on
    // GPU pressure / activity ONLY; the reserved warm is NEVER mixed in here, so
    // "pressure" (cool) stays visually distinct from "needs you" (warm). Lifted by
    // Qt.lighter(…, 1 + 0.3·busy) at the consumer so it brightens with load.
    readonly property color auroraLo:  dark ? "#27306E" : "#3E50C4"   // deep indigo base
    readonly property color auroraMid: dark ? "#4A5AD2" : "#5E54C2"   // blue
    readonly property color auroraHi:  dark ? "#8A6BDC" : "#7E42AE"   // violet crest
}
