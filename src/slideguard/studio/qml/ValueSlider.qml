import QtQuick
import QtQuick.Controls.Basic

Slider {
    id: control
    Theme {
        id: theme
    }
    implicitHeight: theme.controlHeight
    from: 0
    to: 20
    stepSize: 0.1
    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: control.availableWidth
        height: 3
        radius: 1.5
        color: theme.line
        Rectangle {
            width: control.visualPosition * parent.width
            height: parent.height
            radius: 1.5
            color: control.enabled ? theme.accent : theme.disabled
        }
    }
    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: 14
        height: 14
        radius: 7
        color: theme.surface
        border.width: control.activeFocus || control.pressed ? 3 : 2
        border.color: control.enabled ? theme.accent : theme.disabled
    }
}
