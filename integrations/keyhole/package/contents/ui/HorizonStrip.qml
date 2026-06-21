/*
 * HorizonStrip.qml — the signature 2px strip. The ONLY color in the instrument.
 * Samples the Aurora dawn palette via KeyholeModel.horizonColor (busy brightens,
 * warm = single dawn-glow, snag desaturates — NEVER red). Zero GPU: a gradient +
 * an opacity/color tween, no Canvas, no shader.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick

Rectangle {
    id: strip
    property color tint: Qt.rgba(0.10, 0.13, 0.22, 1.0)
    property bool reducedMotion: false
    height: 2
    color: "transparent"

    gradient: Gradient {
        orientation: Gradient.Horizontal
        GradientStop { position: 0.0; color: Qt.darker(strip.tint, 1.6) }
        GradientStop { position: 0.5; color: strip.tint }
        GradientStop { position: 1.0; color: Qt.darker(strip.tint, 1.3) }
    }

    // Color tween: the "sunrise" — interpolate, don't snap. Reduced-motion = instant.
    Behavior on tint {
        enabled: !strip.reducedMotion
        ColorAnimation { duration: 2500; easing.type: Easing.OutCubic }
    }
}
