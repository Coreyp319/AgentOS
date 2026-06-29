/*
 * SidekickCreature.qml — the per-task "sidekick" avatar (ADR-0052), Check-ins tab ONLY.
 *
 * A bounded second visual register beside the calm instrument. Two skins: an ASCII face (a single
 * reactive Text) and a pixel sprite (a Canvas bitmap). Mood is carried REDUNDANTLY by the face
 * SHAPE (+ the card's status word) — never colour-only — so it survives grayscale. All motion runs
 * off ONE shared `tick` (the host's 110ms Timer); reduced-motion / off-screen / over-cap freezes to a
 * representative OPEN-EYED still (everything keys off `_animate`). Poke → squash + a transient "blurt"
 * is VIEW-STATE ONLY (no IPC) and only while animating, so it stays live under the read-only charter
 * without latching on a frozen tick. The face glyphs + Canvas are AT-ignored (decorative; the card's
 * Accessible.name carries the meaning).
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick

Item {
    id: cr
    property var skin
    property string mood: "calm"        // calm | working | stalled | needsyou | done
    property int tick: 0                // the shared clock (host owns the Timer)
    property bool reducedMotion: false
    property string variant: "ascii"    // "ascii" | "pixel"
    property bool animate: true          // caller cap/visibility gate (off-screen / over-cap)
    property int phase: 0                // per-card phase offset so a row doesn't bob/blink in lockstep
    property string blurt: ""            // local speech shown ~7 ticks after a poke
    // The EFFECTIVE motion gate — reduced-motion freezes to a representative OPEN-EYED resting still
    // (the blink/spinner/sway/bob/sparkle/poke all key off this, so the still is the resting face).
    readonly property bool _animate: animate && !reducedMotion

    // poke is view-only state (no system write). Gated on _animate so a reduced-motion / static
    // creature never latches a stuck squash+blurt (a frozen tick can't age the poke out).
    property int _pokeTick: -100
    readonly property int _pokeAge: cr.tick - cr._pokeTick
    readonly property bool poked: cr._animate && cr._pokeAge >= 0 && cr._pokeAge < 7
    readonly property bool showBlurt: poked && cr.blurt.length > 0

    function poke() { cr._pokeTick = cr.tick }

    implicitWidth:  variant === "pixel" ? 44 : 42
    implicitHeight: variant === "pixel" ? 40 : 30

    readonly property color moodColor: {
        if (!skin) return "#9B82E0"
        if (mood === "working")  return skin.creatureWorking
        if (mood === "stalled")  return skin.creatureStalled
        if (mood === "needsyou") return skin.creatureNeedsYou
        if (mood === "done")     return skin.creatureDone
        return skin.creatureCalm
    }
    // The FACE/TEXT fill — AA-safe for needs-you (the register-aware copper split); other moods reuse
    // moodColor. The reserved warm GLOW stays on the halo/border (the card), never the letterforms.
    readonly property color faceColor: (mood === "needsyou" && skin) ? skin.creatureNeedsYouText : moodColor

    // squash-pop on poke (a sine bump over the 7-tick window); 1.0 otherwise.
    readonly property real _pop: {
        if (cr._pokeTick < 0 || cr._pokeAge < 0 || cr._pokeAge >= 7) return 1.0
        return 1.0 + Math.sin(cr._pokeAge / 7 * Math.PI) * 0.18
    }
    readonly property real _swayX: cr._animate ? Math.sin((cr.tick + cr.phase * 5) / 5.0) * 2.0 : 0.0
    readonly property real _bobY:  cr._animate ? Math.sin((cr.tick + cr.phase * 3) / 3.1) * 1.6 : 0.0

    // --- ASCII face: a pure function of (mood, tick, pokeTick) so the binding stays reactive ----
    readonly property var _spin: ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    function faceFor(m, t, pokeTick) {
        var pk = cr._animate && (t - pokeTick) >= 0 && (t - pokeTick) < 7
        if (pk) return "╰(¬‿~)╯"        // ╰(¬‿~)╯ — poke squash face (only while animating)
        var l = "•", mid = "‿", r = "•"       // ( • ‿ • ) resting face
        if (m === "working")  { l = "◔"; r = "◔"; mid = cr._animate ? cr._spin[(t + cr.phase) % 10] : "‿" }
        else if (m === "stalled")  { mid = "﹏" }
        else if (m === "needsyou") { l = "°"; mid = "□"; r = "°" }
        else if (m === "done")     { l = "^"; r = "^" }
        // blink (phase-offset so a row doesn't blink in unison); frozen under reduced motion → open eyes
        if (cr._animate && m !== "needsyou" && ((t + cr.phase * 7) % 26) < 2) { l = "-"; r = "-" }
        var face = "(" + l + mid + r + ")"
        if (m === "needsyou") face += "!"   // STEADY ask, not a ~1Hz nag-blink (ambient grammar)
        return face
    }

    Text {
        id: asciiFace
        visible: cr.variant === "ascii"
        anchors.centerIn: parent
        text: cr.faceFor(cr.mood, cr.tick, cr._pokeTick)
        color: cr.faceColor
        font.family: "monospace"
        font.pixelSize: 15
        font.bold: true
        style: Text.Outline
        styleColor: Qt.rgba(0, 0, 0, 0.35)
        Accessible.ignored: true            // decorative — the card's Accessible.name carries meaning
        transform: [
            Translate { x: cr._swayX; y: cr._bobY },
            Scale {
                origin.x: asciiFace.width / 2; origin.y: asciiFace.height / 2
                xScale: cr._pop; yScale: 2 - cr._pop      // squash: wider → shorter
            }
        ]
    }

    // --- Pixel sprite: a Canvas bitmap (cheap fillRects), repainted on the tick when animating ---
    readonly property var _bitmap: [
        "....XXXX....",
        "..XXXXXXXX..",
        ".XXXXXXXXXX.",
        "XXHXXXXXXXXX",
        "XXXXXXXXXXXX",
        "XXXXXXXXXXXX",
        "XXXXXXXXXXXX",
        ".XXXXXXXXXX.",
        ".X.XX..XX.X."
    ]
    Canvas {
        id: sprite
        visible: cr.variant === "pixel"
        width: cr.implicitWidth
        height: cr.implicitWidth * 9 / 12
        anchors.centerIn: parent
        Accessible.ignored: true
        // repaint when the look changes (mood/tick/poke); bob+squash are transforms, not repaints.
        property int _frame: cr._animate ? cr.tick : 0
        on_FrameChanged: if (visible) requestPaint()
        onVisibleChanged: if (visible) requestPaint()
        Component.onCompleted: requestPaint()
        transform: [
            Translate { x: cr._swayX * 0.6; y: cr._bobY },
            Scale {
                origin.x: sprite.width / 2; origin.y: sprite.height
                xScale: cr._pop; yScale: 2 - cr._pop
            }
        ]
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var cw = width / 12.0, ch = height / 9.0
            var t = cr.tick
            var blink = cr._animate && ((t + cr.phase * 7) % 28) < 2
            for (var y = 0; y < 9; ++y) {
                var rowStr = cr._bitmap[y]
                for (var x = 0; x < 12; ++x) {
                    var c = rowStr.charAt(x)
                    if (c === "X" || c === "H") {
                        ctx.fillStyle = (c === "H") ? Qt.rgba(1, 1, 1, 0.45) : cr.faceColor
                        ctx.fillRect(x * cw, y * ch, cw + 0.5, ch + 0.5)
                    }
                }
            }
            // eyes (dark) unless blinking; row varies by mood (down when stalled, up when done)
            if (!blink) {
                var rows = [4, 5]
                if (cr.mood === "stalled") rows = [5]
                if (cr.mood === "done") rows = [3]
                var cols = [3, 4, 7, 8]
                ctx.fillStyle = cr.skin ? cr.skin.base : "#0b0d14"
                for (var ri = 0; ri < rows.length; ++ri)
                    for (var ci = 0; ci < cols.length; ++ci)
                        ctx.fillRect(cols[ci] * cw, rows[ri] * ch, cw + 0.5, ch + 0.5)
            }
            // working sparkle — slow (~1.8Hz) and motion-gated, never a 4.5Hz strobe (WCAG 2.3.1)
            if (cr._animate && cr.mood === "working" && ((t + cr.phase) % 6) < 1) {
                ctx.fillStyle = "#ffffff"
                ctx.fillRect(9.5 * cw, 0, cw, ch)
            }
            // needs-you alert dot — STEADY (present, not blinking), redundant with the column/word
            if (cr.mood === "needsyou") {
                ctx.fillStyle = cr.faceColor
                ctx.fillRect(5.5 * cw, -ch * 1.2, cw, ch * 2)
            }
        }
    }

    // --- the poke "blurt" speech bubble (transient, view-only) -----------------------------------
    Rectangle {
        id: bubble
        visible: cr.showBlurt
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.top
        anchors.bottomMargin: 1
        width: bubbleText.implicitWidth + 12
        height: bubbleText.implicitHeight + 8
        radius: 7
        color: cr.skin ? cr.skin.deep : "#161A28"
        border.width: 1
        border.color: cr.skin ? cr.skin.hairline : "#262A36"
        z: 5
        Text {
            id: bubbleText
            anchors.centerIn: parent
            text: cr.blurt
            color: cr.skin ? cr.skin.muted : "#B4BAC8"
            font.pixelSize: 10
            font.family: "monospace"
            Accessible.ignored: true
        }
    }
}
