// Spike (v2): does a QtMultimedia video SEAMLESSLY loop in a Plasma-6-shaped QML scene?
//
// This isolates the ONE open question from the video-wallpaper spike: the LOOP SEAM.
// v1 over-coupled it with a ShaderEffect grade (whose inline-.qsb wiring was the likely
// cause of "no window" — a scene-load failure). The grade-over-video seam already "holds
// in principle"; here we show the RAW decoding video full-window so the wrap is judged by
// eye, plus a HUD + stdout log. The renderer questions are identical in a real WallpaperItem
// (both are just the QtQuick scene graph). Run ON-SESSION (offscreen has no GL context).
//
// A solid dark-teal background means the window opened but the video never painted
// (decode problem); a moving gradient means decode + present works and you can judge the
// loop wrap. Auto-quits after ~20s (≈3 loops of the 6s clip).
import QtQuick
import QtMultimedia

Rectangle {
    id: root
    width: 1024; height: 576
    color: "#101418"                    // visible even if the video never paints

    property int  loopWraps: 0
    property real lastPos: 0.0
    property real uBusy: 0.0             // the float lever: busy speeds the loop

    VideoOutput {
        id: vout
        anchors.fill: parent            // visible by default — this is what we watch
    }

    MediaPlayer {
        id: player
        source: "file:///tmp/dream_loop.mp4"
        loops: MediaPlayer.Infinite     // the seamless-loop claim under test
        videoOutput: vout
        audioOutput: null
        Component.onCompleted: play()

        onErrorOccurred: function(err, str) { console.log("SPIKE error=" + err + " '" + str + "'") }
        onPlaybackStateChanged: console.log("SPIKE playbackState=" + playbackState)
        onMediaStatusChanged:  console.log("SPIKE mediaStatus=" + mediaStatus)
        onPositionChanged: {
            if (position + 200 < lastPos) {     // position jumped back near 0 → a loop wrap
                root.loopWraps++
                console.log("SPIKE LOOP WRAP #" + root.loopWraps + " (pos " + lastPos + " -> " + position + ")")
            }
            lastPos = position
        }
    }

    SequentialAnimation on uBusy {
        loops: Animation.Infinite
        NumberAnimation { to: 1.0; duration: 3000 }
        NumberAnimation { to: 0.0; duration: 3000 }
    }
    Binding { target: player; property: "playbackRate"; value: 1.0 + root.uBusy * 0.8 }

    Text {                              // tiny HUD: proves the scene is live pre-first-frame
        anchors { left: parent.left; top: parent.top; margins: 8 }
        color: "white"; font.pixelSize: 14
        text: "loops=" + root.loopWraps + "  state=" + player.playbackState
              + "  status=" + player.mediaStatus + "  rate=" + player.playbackRate.toFixed(2)
    }

    Timer {
        interval: 20000; running: true; repeat: false
        onTriggered: {
            console.log("SPIKE SUMMARY loopWraps=" + root.loopWraps
                + " finalState=" + player.playbackState + " status=" + player.mediaStatus)
            Qt.quit()
        }
    }
}
