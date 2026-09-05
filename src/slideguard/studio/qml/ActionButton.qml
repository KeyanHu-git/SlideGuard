import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Button {
    id: control
    Theme {
        id: theme
    }
    property bool primary: false
    property bool selected: false
    property bool quiet: false
    property string glyph: ""
    property string hint: ""
    implicitHeight: theme.controlHeight
    implicitWidth: Math.max(32, contentItem.implicitWidth + leftPadding + rightPadding)
    leftPadding: 10
    rightPadding: 10
    font.pixelSize: theme.body
    hoverEnabled: true
    Accessible.name: text || hint
    ToolTip.visible: hovered && hint !== ""
    ToolTip.delay: 600
    ToolTip.text: hint
    readonly property color foreground: !enabled ? theme.disabled : primary ? "white" : selected ? theme.accent : theme.ink
    contentItem: RowLayout {
        spacing: 6
        Glyph {
            visible: control.glyph !== ""
            name: control.glyph
            ink: control.foreground
        }
        Text {
            visible: control.text !== ""
            text: control.text
            font: control.font
            color: control.foreground
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            Layout.fillWidth: true
        }
    }
    background: Rectangle {
        radius: theme.radius
        color: !control.enabled ? (control.quiet ? "transparent" : theme.panel) : control.primary ? (control.hovered || control.down ? theme.accentHover : theme.accent) : control.selected ? theme.selection : control.hovered || control.down ? theme.hover : control.quiet ? "transparent" : theme.surface
        border.color: control.activeFocus ? theme.accent : control.quiet || control.primary ? "transparent" : control.selected ? "#bac9f3" : theme.line
        border.width: control.activeFocus ? 2 : 1
    }
}
