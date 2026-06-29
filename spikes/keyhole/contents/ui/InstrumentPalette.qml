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
    readonly property color label:     dark ? "#878C9B" : "#5A6173"   // quiet labels (≥4.5:1; dark was #7A8090/3.86:1, ADR-0031)
    readonly property color dim:       dark ? "#8A90A0" : "#5A6173"   // unknown / snag / idle (light nudged ≥4.5:1)
    readonly property color hairline:  dark ? "#262A36" : "#CFD5E2"   // 1px separators
    readonly property color tintHover: dark ? "#1C2230" : "#DDE2EE"   // row hover

    // --- accents --------------------------------------------------------------
    readonly property color blue:      dark ? "#9B82E0" : "#5A45A6"   // link / acting (contrast-tuned)
    readonly property color warm:      "#FF9957"                       // RESERVED needs-you GLOW/halo hue (both registers)
    // needs-you FOREGROUND (glyph + label text). G7/ADR-0019 F2a: the bright glow hue #FF9957
    // fails AA on the warm-bloomed wallpaper (dark register: 3.07–3.94:1 < 4.5:1) AND on the
    // light register (#FF9957 on #F1F3F8 = 1.90:1). De-couple warm from the text FILL: render
    // label+glyph at the skin text colour (dark #E6E9F0 ≈5.34–6.85:1; light copper #A8480F ~5:1).
    // The warmth now lives ONLY in the aurora ring/halo (`warm` above) + weight — never the letterforms.
    readonly property color warmText:  dark ? "#E6E9F0" : "#A8480F"

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

    // --- creature moods (ADR-0052): the per-task sidekick register, Check-ins tab ONLY ---------
    // ALIASES to existing vetted tokens — NO new hue. needs-you REUSES the reserved `warm`, so the
    // one-warm-element rule holds (a creature never mints a second warm). Mood is ALWAYS paired with
    // shape+text (the face glyphs + the status word), so these never carry state colour-only.
    readonly property color creatureWorking:  blue       // active — the unified violet accent
    // stalled/done are used as STATUS TEXT on the card glass; the shared stUp/stAmber light values are
    // razor-thin AA there (4.68 / 4.52:1), so give the creature versions a darker LIGHT value for real
    // headroom (~5.3:1) WITHOUT touching the shared SYSTEM-board tokens. Dark register is unchanged.
    readonly property color creatureStalled:  dark ? stAmber : "#7A5710"   // blocked / failing — amber (never red)
    readonly property color creatureNeedsYou: warm       // RESERVED needs-you GLOW/halo/border + dot (no second warm)
    readonly property color creatureDone:     dark ? stUp : "#1F6B41"      // finished — muted sage
    readonly property color creatureCalm:     dim        // queued / scheduled — quiet
    // needs-you FOREGROUND (the face glyph + the status WORD) — the AA-safe `warmText` split, exactly
    // like the tray glyph (the raw glow #FF9957 fails AA as text on the light base, 1.83:1). Dark keeps
    // the glow; light becomes copper #A8480F (~5:1). The halo/border/dot stay `creatureNeedsYou`.
    readonly property color creatureNeedsYouText: dark ? warm : warmText
}
