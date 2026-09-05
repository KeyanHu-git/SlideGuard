import QtQuick
import QtQuick.Controls.Basic

Button {
    id: control
    property bool primary: false
    property bool selected: false
    implicitHeight: 38
    leftPadding: 14
    rightPadding: 14
    font.pixelSize: 13
    Accessible.name: text
    contentItem: Text {
        text: control.text
        font: control.font
        color: !control.enabled ? "#9ba4ad" : control.primary ? "white" : "#253446"
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: 8
        color: !control.enabled ? "#edf0f3" : control.primary ? (control.down ? "#075e54" : "#087f70") : control.selected ? "#dcefe9" : control.hovered ? "#edf2f5" : "#ffffff"
        border.color: control.activeFocus ? "#087f70" : control.selected ? "#87baad" : "#dce2e7"
        border.width: control.activeFocus ? 2 : 1
    }
}
