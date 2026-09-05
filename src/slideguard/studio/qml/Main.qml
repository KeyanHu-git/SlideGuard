import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ApplicationWindow {
    id: window
    width: 1400
    height: 880
    minimumWidth: 1000
    minimumHeight: 700
    visible: true
    title: "SlideGuard Studio · 可视导出工作台（开发版）"
    color: "#f4f6f8"
    font.family: "Microsoft YaHei UI"
    font.pixelSize: 13
    property var doc: studio.state
    property bool linked: true
    onClosing: close => {
        close.accepted = studio.canClose();
    }

    Shortcut {
        sequence: "Ctrl+O"
        enabled: !doc.busy
        onActivated: studio.chooseFile()
    }
    Shortcut {
        sequence: "Ctrl+Z"
        enabled: !doc.busy
        onActivated: studio.undo(false)
    }
    Shortcut {
        sequence: "Ctrl+Shift+Z"
        enabled: !doc.busy
        onActivated: studio.undo(true)
    }
    Shortcut {
        sequence: "Ctrl+0"
        onActivated: viewport.fitPage()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 76
            color: "#ffffff"
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 24
                anchors.rightMargin: 24
                spacing: 16
                Rectangle {
                    width: 38
                    height: 38
                    radius: 11
                    color: "#087f70"
                    Text {
                        anchors.centerIn: parent
                        text: "S"
                        color: "white"
                        font.pixelSize: 24
                        font.bold: true
                    }
                }
                ColumnLayout {
                    spacing: 2
                    Text {
                        text: "SlideGuard Studio"
                        font.pixelSize: 19
                        font.bold: true
                        color: "#203345"
                    }
                    Text {
                        text: "裁剪清楚，再交付"
                        font.pixelSize: 11
                        color: "#758493"
                    }
                }
                Rectangle {
                    width: 1
                    height: 30
                    color: "#e0e6eb"
                }
                Text {
                    Layout.fillWidth: true
                    text: doc.filename
                    elide: Text.ElideMiddle
                    color: "#4b5d6f"
                }
                ActionButton {
                    text: "打开 PPTX"
                    enabled: !doc.busy
                    onClicked: studio.chooseFile()
                }
                Label {
                    text: "本地处理 · 开发预览"
                    color: "#71818f"
                    font.pixelSize: 11
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: "#e0e6eb"
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            Rectangle {
                Layout.preferredWidth: 112
                Layout.fillHeight: true
                color: "#f8fafb"
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 12
                    Text {
                        text: "页面"
                        color: "#738291"
                        font.pixelSize: 12
                    }
                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 8
                        model: doc.pages
                        delegate: ActionButton {
                            required property int index
                            width: ListView.view.width
                            height: 62
                            text: "第 " + (index + 1) + " 页"
                            selected: doc.page === index + 1
                            enabled: !doc.busy
                            onClicked: {
                                studio.selectPage(index + 1);
                                viewport.fitPage();
                            }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "当前切片：单页导出"
                        wrapMode: Text.WordWrap
                        color: "#8995a1"
                        font.pixelSize: 11
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 62
                    color: "#f8fafb"
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 6
                        ActionButton {
                            text: "裁剪"
                            selected: !viewport.handTool
                            onClicked: viewport.handTool = false
                        }
                        ActionButton {
                            text: "平移"
                            selected: viewport.handTool
                            onClicked: viewport.handTool = true
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        ActionButton {
                            text: "−"
                            onClicked: viewport.zoomAt(viewport.zoom / 1.25, viewport.width / 2, viewport.height / 2)
                        }
                        Label {
                            text: Math.round(viewport.zoom * viewport.fit * 100) + "%"
                            Layout.preferredWidth: 45
                            horizontalAlignment: Text.AlignHCenter
                            color: "#4b5d6f"
                        }
                        ActionButton {
                            text: "+"
                            onClicked: viewport.zoomAt(viewport.zoom * 1.25, viewport.width / 2, viewport.height / 2)
                        }
                        ActionButton {
                            text: "适配"
                            onClicked: viewport.fitPage()
                        }
                        ActionButton {
                            text: "1:1"
                            onClicked: viewport.actualPixels()
                        }
                    }
                }
                CropViewport {
                    id: viewport
                    objectName: "cropViewport"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    controller: studio
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 54
                    color: "#f8fafb"
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 9
                        spacing: 6
                        ActionButton {
                            text: "源参考"
                            selected: doc.viewKind === "source"
                            enabled: !doc.busy && doc.ready
                            onClicked: {
                                studio.showView("source");
                                viewport.fitPage();
                            }
                        }
                        ActionButton {
                            text: "交付 PDF"
                            selected: doc.viewKind === "pdf"
                            enabled: !doc.busy && doc.hasResult
                            onClicked: {
                                studio.showView("pdf");
                                viewport.fitPage();
                            }
                        }
                        ActionButton {
                            text: "透明结果"
                            selected: doc.viewKind === "alpha"
                            enabled: !doc.busy && doc.hasResult
                            onClicked: {
                                studio.showView("alpha");
                                viewport.fitPage();
                            }
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        ComboBox {
                            implicitWidth: 96
                            model: ["棋盘格", "白底", "深色底"]
                            onActivated: viewport.backdrop = ["checker", "white", "dark"][currentIndex]
                            Accessible.name: "预览背景"
                        }
                    }
                }
            }
            Rectangle {
                Layout.preferredWidth: 310
                Layout.fillHeight: true
                color: "white"
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 12
                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                            width: parent.width
                            spacing: 14
                            Label {
                                text: "01  调整边界"
                                font.pixelSize: 16
                                font.bold: true
                                color: "#25384b"
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 5
                                ActionButton {
                                    text: "自动紧边"
                                    selected: doc.mode === "auto"
                                    enabled: !doc.busy && doc.ready && doc.viewKind === "source"
                                    onClicked: studio.setMode("auto")
                                }
                                ActionButton {
                                    text: "整页"
                                    enabled: !doc.busy && doc.ready && doc.viewKind === "source"
                                    onClicked: studio.setMode("full")
                                }
                                ActionButton {
                                    text: "手动"
                                    selected: doc.mode === "manual"
                                    enabled: !doc.busy && doc.ready && doc.viewKind === "source"
                                    onClicked: studio.setMode("manual")
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                text: "拖动紫色手柄调整裁剪。绿色外框就是扩边后的输出范围。"
                                wrapMode: Text.WordWrap
                                color: "#728191"
                                font.pixelSize: 12
                            }
                            RowLayout {
                                Label {
                                    text: "边距扩展"
                                    color: "#34485c"
                                    Layout.fillWidth: true
                                }
                                Switch {
                                    text: "四边联动"
                                    checked: window.linked
                                    onToggled: {
                                        window.linked = checked;
                                        if (checked)
                                            studio.setMargin(-1, doc.margins[0]);
                                    }
                                    enabled: !doc.busy && doc.viewKind === "source"
                                }
                            }
                            RowLayout {
                                spacing: 6
                                Repeater {
                                    model: [0, 1, 2, 5]
                                    ActionButton {
                                        required property int modelData
                                        text: modelData + "%"
                                        selected: doc.margins.every(v => Math.abs(v - modelData) < 0.001)
                                        enabled: !doc.busy && doc.ready && doc.viewKind === "source"
                                        onClicked: studio.setMargin(-1, modelData)
                                    }
                                }
                            }
                            Repeater {
                                model: window.linked ? 1 : 4
                                ColumnLayout {
                                    required property int index
                                    Layout.fillWidth: true
                                    spacing: 0
                                    Label {
                                        text: (window.linked ? "全部" : ["左边", "上边", "右边", "下边"][index]) + "  " + doc.margins[index].toFixed(1) + "%"
                                        color: "#617486"
                                        font.pixelSize: 12
                                    }
                                    Slider {
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 20
                                        stepSize: 0.1
                                        value: doc.margins[index]
                                        enabled: !doc.busy && doc.ready && doc.viewKind === "source"
                                        Accessible.name: window.linked ? "四边扩展百分比" : ["左边扩展", "上边扩展", "右边扩展", "下边扩展"][index]
                                        onPressedChanged: {
                                            if (pressed)
                                                studio.beginEdit();
                                            else
                                                studio.endEdit(false);
                                        }
                                        onMoved: studio.setMargin(window.linked ? -1 : index, value)
                                    }
                                }
                            }
                            Label {
                                text: doc.cropSize
                                color: "#087f70"
                                font.pixelSize: 12
                            }
                            Label {
                                Layout.fillWidth: true
                                text: "按选区宽高计算扩展；到幻灯片边缘会停止。"
                                wrapMode: Text.WordWrap
                                font.pixelSize: 11
                                color: "#7b8894"
                            }
                            RowLayout {
                                ActionButton {
                                    text: "撤销"
                                    enabled: doc.canUndo && !doc.busy && doc.viewKind === "source"
                                    onClicked: studio.undo(false)
                                }
                                ActionButton {
                                    text: "重做"
                                    enabled: doc.canRedo && !doc.busy && doc.viewKind === "source"
                                    onClicked: studio.undo(true)
                                }
                            }
                            CheckBox {
                                id: exact
                                text: "精确坐标（可选）"
                            }
                            GridLayout {
                                visible: exact.checked
                                columns: 2
                                Layout.fillWidth: true
                                Repeater {
                                    model: 4
                                    ColumnLayout {
                                        required property int index
                                        Label {
                                            text: ["左 %", "上 %", "右 %", "下 %"][index]
                                            font.pixelSize: 11
                                        }
                                        TextField {
                                            Layout.preferredWidth: 110
                                            text: (doc.base[index] * 100).toFixed(3)
                                            enabled: !doc.busy && doc.ready && doc.viewKind === "source"
                                            validator: DoubleValidator {
                                                bottom: 0
                                                top: 100
                                                decimals: 3
                                                locale: "C"
                                            }
                                            onEditingFinished: {
                                                if (acceptableInput)
                                                    studio.setBound(index, Number(text));
                                            }
                                        }
                                    }
                                }
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                height: 1
                                color: "#e7edf1"
                            }
                            Label {
                                text: "02  输出与验证"
                                font.pixelSize: 16
                                font.bold: true
                                color: "#25384b"
                            }
                            RowLayout {
                                Label {
                                    text: "紧凑版上限"
                                    Layout.fillWidth: true
                                    color: "#617486"
                                }
                                ComboBox {
                                    model: ["1 MB", "2.5 MB", "5 MB", "10 MB"]
                                    currentIndex: [1, 2.5, 5, 10].indexOf(doc.limit)
                                    implicitWidth: 106
                                    enabled: !doc.busy && doc.ready && doc.viewKind === "source"
                                    onActivated: studio.setBudget([1, 2.5, 5, 10][currentIndex])
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                text: "完整SVG另行保留。紧凑PDF/SVG可能缩小并有损压缩图片，不是把位图变矢量。"
                                wrapMode: Text.WordWrap
                                font.pixelSize: 11
                                color: "#7b8894"
                            }
                            ActionButton {
                                Layout.fillWidth: true
                                text: "选择输出文件夹"
                                enabled: !doc.busy
                                onClicked: studio.chooseOutput()
                            }
                            Label {
                                Layout.fillWidth: true
                                text: doc.output
                                elide: Text.ElideMiddle
                                font.pixelSize: 11
                                color: "#7b8894"
                                ToolTip.visible: pathHover.hovered
                                ToolTip.text: doc.output
                                HoverHandler {
                                    id: pathHover
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                text: doc.resultSummary
                                wrapMode: Text.WordWrap
                                color: "#455d70"
                                font.pixelSize: 12
                            }
                            RowLayout {
                                visible: doc.hasResult
                                ActionButton {
                                    text: "复核产物"
                                    enabled: !doc.busy
                                    onClicked: studio.verifyResult()
                                }
                                ActionButton {
                                    text: "验收报告"
                                    onClicked: studio.openResult("report")
                                }
                            }
                            ActionButton {
                                Layout.fillWidth: true
                                visible: doc.hasResult
                                text: "打开结果文件夹"
                                onClicked: studio.openResult("folder")
                            }
                        }
                    }
                    ActionButton {
                        Layout.fillWidth: true
                        text: "检查当前参数"
                        enabled: !doc.busy && doc.ready
                        onClicked: studio.export(true)
                    }
                    Label {
                        Layout.fillWidth: true
                        text: doc.checkStatus
                        wrapMode: Text.WordWrap
                        color: "#6c7f8e"
                        font.pixelSize: 11
                    }
                    ActionButton {
                        Layout.fillWidth: true
                        implicitHeight: 48
                        primary: true
                        text: doc.operation === "export" ? "安全取消导出" : "导出当前页并自动验收"
                        enabled: doc.operation === "export" || (!doc.busy && doc.ready)
                        onClicked: doc.operation === "export" ? studio.cancel() : studio.export(false)
                    }
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 42
            color: "#ffffff"
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 20
                anchors.rightMargin: 20
                spacing: 10
                BusyIndicator {
                    running: doc.busy
                    visible: doc.busy
                    implicitWidth: 24
                    implicitHeight: 24
                }
                Text {
                    Layout.fillWidth: true
                    text: doc.status
                    elide: Text.ElideRight
                    color: "#52697b"
                    font.pixelSize: 12
                }
                Text {
                    text: doc.busy ? "已运行 " + doc.elapsed + " 秒" : "本地执行 · 不上传稿件"
                    color: "#8995a1"
                    font.pixelSize: 11
                }
            }
        }
    }
}
