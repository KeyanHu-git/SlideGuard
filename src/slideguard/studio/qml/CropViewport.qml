import QtQuick
import QtQuick.Controls.Basic

Rectangle {
    id: viewport
    Theme {
        id: theme
    }
    required property var controller
    property var doc: controller.state
    property real zoom: 1
    property real panX: 0
    property real panY: 0
    property bool handTool: false
    property bool spaceHeld: false
    property string backdrop: "checker"
    property real fit: Math.min(Math.max(1, width - 80) / doc.imageWidth, Math.max(1, height - 80) / doc.imageHeight)
    property int renderWidth: 1600
    property string fittedUrl: ""
    property bool editable: doc.ready && !doc.busy && doc.viewKind === "source"
    property bool panning: handTool || spaceHeld
    color: theme.canvas
    clip: true
    focus: true
    Accessible.name: "预览画布：滚轮缩放，空格拖动平移，拖动蓝色手柄裁剪"

    function fitPage() {
        zoom = 1;
        panX = 0;
        panY = 0;
    }
    function fitContent() {
        if (!doc.ready || doc.viewKind !== "source") {
            fitPage();
            return;
        }
        let w = (doc.effective[2] - doc.effective[0]) * doc.imageWidth;
        let h = (doc.effective[3] - doc.effective[1]) * doc.imageHeight;
        zoom = Math.max(0.15, Math.min(32, Math.min((width - 80) / Math.max(1, w), (height - 80) / Math.max(1, h)) / fit));
        panX = (0.5 - (doc.effective[0] + doc.effective[2]) / 2) * doc.imageWidth * fit * zoom;
        panY = (0.5 - (doc.effective[1] + doc.effective[3]) / 2) * doc.imageHeight * fit * zoom;
    }
    function actualPixels() {
        zoomAt(1 / fit, width / 2, height / 2);
    }
    function zoomAt(value, x, y) {
        let next = Math.max(0.15, Math.min(32, value));
        let ratio = next / zoom;
        panX = (x - width / 2) * (1 - ratio) + panX * ratio;
        panY = (y - height / 2) * (1 - ratio) + panY * ratio;
        zoom = next;
    }
    onZoomChanged: renderDelay.restart()
    onWidthChanged: renderDelay.restart()
    onHeightChanged: renderDelay.restart()
    Connections {
        target: viewport.controller
        function onChanged() {
            renderDelay.restart();
            if (viewport.doc.ready && viewport.doc.previewUrl !== viewport.fittedUrl) {
                viewport.fittedUrl = viewport.doc.previewUrl;
                Qt.callLater(viewport.fitContent);
            }
        }
    }
    Timer {
        id: renderDelay
        interval: 140
        onTriggered: viewport.renderWidth = Math.min(4096, Math.max(640, Math.ceil(page.width * Screen.devicePixelRatio / 256) * 256))
    }
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Space) {
            spaceHeld = true;
            event.accepted = true;
        } else if (event.key === Qt.Key_Escape) {
            controller.endEdit(true);
            event.accepted = true;
        } else if (editable && [Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down].indexOf(event.key) >= 0) {
            let n = event.modifiers & Qt.ShiftModifier ? 10 : 1;
            controller.moveCrop((event.key === Qt.Key_Right ? n : event.key === Qt.Key_Left ? -n : 0) / doc.imageWidth, (event.key === Qt.Key_Down ? n : event.key === Qt.Key_Up ? -n : 0) / doc.imageHeight);
            event.accepted = true;
        }
    }
    Keys.onReleased: event => {
        if (event.key === Qt.Key_Space) {
            spaceHeld = false;
            event.accepted = true;
        }
    }
    onActiveFocusChanged: {
        if (!activeFocus)
            spaceHeld = false;
    }

    Item {
        id: page
        width: doc.imageWidth * viewport.fit * viewport.zoom
        height: doc.imageHeight * viewport.fit * viewport.zoom
        x: (viewport.width - width) / 2 + viewport.panX
        y: (viewport.height - height) / 2 + viewport.panY
        visible: doc.ready && doc.previewUrl !== ""
        Rectangle {
            anchors.fill: parent
            color: viewport.backdrop === "dark" ? "#252d37" : "white"
        }
        Image {
            anchors.fill: parent
            visible: viewport.backdrop === "checker" && doc.viewKind === "alpha"
            source: "checker.svg"
            sourceSize: Qt.size(32, 32)
            fillMode: Image.Tile
        }
        Image {
            id: artwork
            objectName: "previewImage"
            anchors.fill: parent
            source: doc.previewUrl
            sourceSize.width: viewport.renderWidth
            asynchronous: true
            cache: false
            smooth: true
            fillMode: Image.Stretch
        }
        Rectangle {
            anchors.fill: parent
            color: "transparent"
            border.color: theme.line
        }
        // Dim only outside the effective crop. The authoring image is untouched.
        Repeater {
            model: doc.viewKind === "source" ? 4 : 0
            Rectangle {
                required property int index
                color: theme.cropMask
                x: index === 3 ? doc.effective[2] * page.width : 0
                y: index === 0 ? 0 : index === 1 ? doc.effective[3] * page.height : doc.effective[1] * page.height
                width: index < 2 ? page.width : index === 2 ? doc.effective[0] * page.width : (1 - doc.effective[2]) * page.width
                height: index === 0 ? doc.effective[1] * page.height : index === 1 ? (1 - doc.effective[3]) * page.height : (doc.effective[3] - doc.effective[1]) * page.height
            }
        }
        Rectangle {
            visible: doc.viewKind === "source"
            x: doc.effective[0] * page.width
            y: doc.effective[1] * page.height
            width: (doc.effective[2] - doc.effective[0]) * page.width
            height: (doc.effective[3] - doc.effective[1]) * page.height
            color: "transparent"
            border.width: 2
            border.color: theme.output
        }
        Rectangle {
            id: crop
            visible: doc.viewKind === "source"
            x: doc.base[0] * page.width
            y: doc.base[1] * page.height
            width: (doc.base[2] - doc.base[0]) * page.width
            height: (doc.base[3] - doc.base[1]) * page.height
            color: "transparent"
            border.color: theme.accent
            border.width: 1
            MouseArea {
                anchors.fill: parent
                enabled: viewport.editable && !viewport.panning
                cursorShape: Qt.SizeAllCursor
                property point last
                onPressed: mouse => {
                    viewport.forceActiveFocus();
                    last = mapToItem(viewport, mouse.x, mouse.y);
                    controller.beginEdit();
                }
                onPositionChanged: mouse => {
                    if (!pressed)
                        return;
                    let current = mapToItem(viewport, mouse.x, mouse.y);
                    controller.moveCrop((current.x - last.x) / page.width, (current.y - last.y) / page.height);
                    last = current;
                }
                onReleased: controller.endEdit(false)
                onCanceled: controller.endEdit(true)
            }
            Repeater {
                model: [
                    {
                        name: "nw",
                        x: 0,
                        y: 0
                    },
                    {
                        name: "n",
                        x: .5,
                        y: 0
                    },
                    {
                        name: "ne",
                        x: 1,
                        y: 0
                    },
                    {
                        name: "e",
                        x: 1,
                        y: .5
                    },
                    {
                        name: "se",
                        x: 1,
                        y: 1
                    },
                    {
                        name: "s",
                        x: .5,
                        y: 1
                    },
                    {
                        name: "sw",
                        x: 0,
                        y: 1
                    },
                    {
                        name: "w",
                        x: 0,
                        y: .5
                    }
                ]
                Rectangle {
                    required property var modelData
                    width: 8
                    height: 8
                    radius: 1
                    x: modelData.x * crop.width - width / 2
                    y: modelData.y * crop.height - height / 2
                    color: "white"
                    border.color: theme.accent
                    border.width: 1
                    MouseArea {
                        anchors.fill: parent
                        anchors.margins: -8
                        enabled: viewport.editable && !viewport.panning
                        cursorShape: modelData.x === .5 ? Qt.SizeVerCursor : modelData.y === .5 ? Qt.SizeHorCursor : Qt.SizeFDiagCursor
                        onPressed: {
                            viewport.forceActiveFocus();
                            controller.beginEdit();
                        }
                        onPositionChanged: mouse => {
                            if (!pressed)
                                return;
                            let p = mapToItem(page, mouse.x, mouse.y);
                            controller.resizeCrop(modelData.name, p.x / page.width, p.y / page.height);
                        }
                        onReleased: controller.endEdit(false)
                        onCanceled: controller.endEdit(true)
                    }
                }
            }
        }
    }
    MouseArea {
        anchors.fill: parent
        acceptedButtons: viewport.panning ? Qt.AllButtons : Qt.MiddleButton | Qt.RightButton
        cursorShape: viewport.panning ? Qt.OpenHandCursor : Qt.ArrowCursor
        property point last
        onPressed: mouse => {
            viewport.forceActiveFocus();
            last = Qt.point(mouse.x, mouse.y);
        }
        onPositionChanged: mouse => {
            if (!pressed)
                return;
            viewport.panX += mouse.x - last.x;
            viewport.panY += mouse.y - last.y;
            last = Qt.point(mouse.x, mouse.y);
        }
        onWheel: wheel => {
            viewport.zoomAt(viewport.zoom * Math.pow(1.0015, wheel.angleDelta.y), wheel.x, wheel.y);
            wheel.accepted = true;
        }
        onDoubleClicked: viewport.fitPage()
    }
    Column {
        anchors.centerIn: parent
        spacing: 16
        visible: !doc.ready
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: doc.busy ? "正在读取幻灯片" : "打开 PowerPoint 文件"
            font.pixelSize: 20
            color: theme.ink
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: doc.busy ? "使用 PowerPoint 生成参考图" : "选择页面，调整边界，导出 PDF / SVG"
            font.pixelSize: 12
            color: theme.secondary
        }
        ActionButton {
            anchors.horizontalCenter: parent.horizontalCenter
            visible: !doc.busy
            glyph: "folder"
            text: "选择 PPTX"
            onClicked: controller.chooseFile()
        }
    }
    Label {
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        anchors.margins: 14
        padding: 6
        visible: doc.ready
        font.pixelSize: theme.small
        text: doc.viewKind === "source" ? "源图白底参考 · 拖动裁剪 / 空格平移" : doc.viewKind === "pdf" ? "PDF 预览 · 显示上限 4096 px" : "透明 PNG · 棋盘格不写入文件"
        color: theme.secondary
        background: Rectangle {
            color: "#eaffffff"
            radius: 3
        }
    }
}
