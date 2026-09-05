import QtQuick
import QtQuick.Controls.Basic

ComboBox {
    id: control
    Theme {
        id: theme
    }
    implicitHeight: theme.controlHeight
    implicitWidth: 110
    font.pixelSize: theme.body
    leftPadding: 10
    rightPadding: 26
    hoverEnabled: true
    contentItem: Text {
        text: control.displayText
        font: control.font
        color: control.enabled ? theme.ink : theme.disabled
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Glyph {
        name: "down"
        ink: theme.secondary
        x: control.width - width - 7
        y: (control.height - height) / 2
    }
    background: Rectangle {
        color: control.hovered ? theme.hover : theme.surface
        radius: theme.radius
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? theme.accent : theme.line
    }
    delegate: ItemDelegate {
        required property var modelData
        required property int index
        width: control.width
        height: theme.controlHeight
        text: modelData
        font.pixelSize: theme.body
        highlighted: control.highlightedIndex === index
        palette.highlight: theme.selection
        palette.highlightedText: theme.ink
    }
    popup: Popup {
        y: control.height + 4
        width: control.width
        padding: 4
        implicitHeight: contentItem.implicitHeight + 8
        background: Rectangle {
            color: theme.surface
            radius: theme.radius
            border.color: theme.line
        }
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
        }
    }
}
