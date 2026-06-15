// SPIKE harness — render the modified Hills (aurora style 1) at a FIXED time, with the
// agent state taken from agent_data.js (written per-run by run.sh from the same values
// as the canonical agent.json). Bare QtQuick so it runs under a plain `qml6`.
// Fixed iTime makes the captures differ ONLY by agent state.
import QtQuick
import "agent_data.js" as Agent

Window {
    id: win
    visible: true
    width: 1024; height: 576
    color: "black"
    title: "hills-reactive spike"

    Rectangle { id: reactBlack; width: 8; height: 8; color: "black"; visible: false }
    ShaderEffectSource {
        id: reactSrc; sourceItem: reactBlack; live: false; hideSource: true
        textureSize: Qt.size(8, 8)
    }

    ShaderEffect {
        id: fx
        anchors.fill: parent
        fragmentShader: "aurora.frag.qsb"
        // uniforms map by NAME to the shader's std140 block; unspecified ones default to 0.
        property real     iTime: 8.0                 // fixed -> deterministic comparison
        property vector2d iResolution: Qt.vector2d(win.width, win.height)
        property vector2d iMouse: Qt.vector2d(0.5, 0.5)
        property real     iMouseActive: 0.0
        property real     uSpeed: 1.0
        property real     uInteractivity: 0.0
        property real     uDark: 1.0
        property real     uIntensity: 1.0
        property int      uTheme: 0                  // Big Sur
        property int      uStyle: 0                  // Flow
        property real     uWinReact: 0.0
        property int      uWinCount: 0
        property real     uActiveMove: 0.0
        property real     uMusicReact: 0.0
        property real     uYaw: 0.0
        property real     uPitch: 0.0
        property real     uHill: 0.0
        property variant  reactTex: reactSrc
        // --- the spike: agent state -> Hills look ---
        property int      uAgentState: Agent.state
        property real     uAgentBusy:  Agent.busy
        property real     uAgentWarm:  Agent.warm
        property real     uAgentSnag:  Agent.snag
    }

    Component.onCompleted:
        console.log("AGENT busy=" + Agent.busy + " warm=" + Agent.warm + " snag=" + Agent.snag)

    Timer {
        interval: 700; running: true; repeat: false
        onTriggered: fx.grabToImage(function (res) {
            res.saveToFile("out.png");
            Qt.callLater(Qt.quit);
        })
    }
}
