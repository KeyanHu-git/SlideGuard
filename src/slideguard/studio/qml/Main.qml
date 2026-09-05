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
    title: "SlideGuard Studio · 设计预览"
    color: theme.surface
    font.family: "Microsoft YaHei UI"
    font.pixelSize: theme.body
    palette.windowText: theme.ink
    palette.text: theme.ink
    palette.highlight: theme.accent
    Theme {
        id: theme
    }
    property var doc: studio.state
    property bool linked: true
    readonly property bool editing: doc.ready && !doc.busy && doc.viewKind === "source"
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
        enabled: window.editing
        onActivated: studio.undo(false)
    }
    Shortcut {
        sequence: "Ctrl+Shift+Z"
        enabled: window.editing
        onActivated: studio.undo(true)
    }
    Shortcut {
        sequence: "Ctrl+0"
        onActivated: viewport.fitContent()
    }

    component Caption: Label {
        color: theme.secondary
        font.pixelSize: theme.small
        wrapMode: Text.WordWrap
    }
    component SectionTitle: Label {
        color: theme.ink
        font.pixelSize: theme.heading
        font.weight: Font.DemiBold
    }
    component Rule: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: theme.line
    }
    component Separator: Rectangle {
        implicitWidth: 1
        implicitHeight: 18
        color: theme.line
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            objectName: "documentBar"
            Layout.fillWidth: true
            implicitHeight: 48
            color: theme.surface
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 16
                Label {
                    text: "SlideGuard"
                    font.family: "Segoe UI"
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                    color: theme.ink
                }
                Separator {}
                Glyph {
                    name: "document"
                    ink: theme.secondary
                }
                Label {
                    Layout.fillWidth: true
                    text: doc.filename
                    elide: Text.ElideMiddle
                    color: theme.ink
                    ToolTip.visible: fileHover.hovered
                    ToolTip.text: doc.source
                    HoverHandler {
                        id: fileHover
                    }
                }
                Caption {
                    text: "PPTX"
                    visible: doc.source !== ""
                }
                ActionButton {
                    glyph: "folder"
                    text: "打开"
                    hint: "打开 PowerPoint · Ctrl+O"
                    enabled: !doc.busy
                    onClicked: studio.chooseFile()
                }
            }
        }
        Rule {}
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            Rectangle {
                objectName: "pageRail"
                Layout.preferredWidth: window.width < 1150 ? 132 : 168
                Layout.fillHeight: true
                color: theme.panel
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        SectionTitle {
                            text: "页面"
                            Layout.fillWidth: true
                        }
                        Caption {
                            text: doc.pages > 0 ? String(doc.pages) : ""
                        }
                    }
                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        model: doc.pages
                        clip: true
                        spacing: 10
                        delegate: Item {
                            id: pageEntry
                            required property int index
                            activeFocusOnTab: true
                            enabled: !doc.busy
                            Accessible.role: Accessible.Button
                            Accessible.name: "第 " + (index + 1) + " 页"
                            Accessible.onPressAction: openPage()
                            function openPage() {
                                studio.selectPage(index + 1);
                                viewport.fitContent();
                            }
                            Keys.onReturnPressed: openPage()
                            Keys.onSpacePressed: openPage()
                            width: ListView.view.width
                            height: width * 0.5625 + 32
                            Rectangle {
                                id: thumbnail
                                width: parent.width
                                height: parent.width * 0.5625
                                color: theme.surface
                                radius: 2
                                border.width: doc.page === index + 1 || pageEntry.activeFocus ? 2 : 1
                                border.color: doc.page === index + 1 || pageEntry.activeFocus ? theme.accent : theme.line
                                Image {
                                    objectName: "pageThumbnail"
                                    anchors.fill: parent
                                    anchors.margins: 4
                                    source: doc.page === index + 1 ? doc.sourcePreviewUrl : ""
                                    sourceSize.width: 280
                                    fillMode: Image.PreserveAspectFit
                                    asynchronous: true
                                }
                                Caption {
                                    anchors.centerIn: parent
                                    visible: doc.page !== index + 1
                                    text: "点击预览"
                                }
                            }
                            Label {
                                anchors.top: thumbnail.bottom
                                anchors.topMargin: 7
                                text: String(index + 1).padStart(2, "0")
                                font.pixelSize: 11
                                color: doc.page === index + 1 ? theme.accent : theme.secondary
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: !doc.busy
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    pageEntry.forceActiveFocus();
                                    pageEntry.openPage();
                                }
                            }
                        }
                    }
                    Caption {
                        Layout.fillWidth: true
                        text: "单页导出\n原稿不会被改写"
                    }
                }
            }
            Rectangle {
                Layout.fillHeight: true
                implicitWidth: 1
                color: theme.line
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0
                Rectangle {
                    objectName: "canvasToolbar"
                    Layout.fillWidth: true
                    implicitHeight: 44
                    color: theme.surface
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        spacing: 4
                        ActionButton {
                            glyph: "crop"
                            text: window.width >= 1200 ? "裁剪" : ""
                            hint: "拖动边界裁剪"
                            quiet: true
                            selected: !viewport.handTool
                            enabled: window.editing
                            onClicked: viewport.handTool = false
                        }
                        ActionButton {
                            glyph: "hand"
                            hint: "平移 · 按住空格也可拖动"
                            quiet: true
                            selected: viewport.handTool
                            onClicked: viewport.handTool = true
                        }
                        Separator {}
                        ActionButton {
                            glyph: "undo"
                            hint: "撤销 · Ctrl+Z"
                            quiet: true
                            enabled: window.editing && doc.canUndo
                            onClicked: studio.undo(false)
                        }
                        ActionButton {
                            glyph: "redo"
                            hint: "重做 · Ctrl+Shift+Z"
                            quiet: true
                            enabled: window.editing && doc.canRedo
                            onClicked: studio.undo(true)
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        ActionButton {
                            glyph: "minus"
                            hint: "缩小"
                            quiet: true
                            onClicked: viewport.zoomAt(viewport.zoom / 1.25, viewport.width / 2, viewport.height / 2)
                        }
                        Label {
                            text: Math.round(viewport.zoom * viewport.fit * 100) + "%"
                            horizontalAlignment: Text.AlignHCenter
                            Layout.preferredWidth: 42
                            color: theme.secondary
                        }
                        ActionButton {
                            glyph: "plus"
                            hint: "放大 · 滚轮以鼠标为中心"
                            quiet: true
                            onClicked: viewport.zoomAt(viewport.zoom * 1.25, viewport.width / 2, viewport.height / 2)
                        }
                        Separator {}
                        ActionButton {
                            glyph: "fit"
                            hint: "适合选区 · Ctrl+0"
                            quiet: true
                            onClicked: viewport.fitContent()
                        }
                        ActionButton {
                            text: "1:1"
                            hint: "参考图实际像素"
                            quiet: true
                            onClicked: viewport.actualPixels()
                        }
                    }
                }
                Rule {}
                CropViewport {
                    id: viewport
                    objectName: "cropViewport"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    controller: studio
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 44
                    color: theme.surface
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        spacing: 2
                        Repeater {
                            model: [
                                {
                                    key: "source",
                                    label: "源图"
                                },
                                {
                                    key: "pdf",
                                    label: "PDF"
                                },
                                {
                                    key: "alpha",
                                    label: "透明 PNG"
                                }
                            ]
                            ActionButton {
                                required property var modelData
                                text: modelData.label
                                quiet: true
                                selected: doc.viewKind === modelData.key
                                enabled: !doc.busy && (modelData.key === "source" ? doc.ready : doc.hasResult)
                                onClicked: {
                                    studio.showView(modelData.key);
                                    viewport.fitContent();
                                }
                            }
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        Choice {
                            implicitWidth: 94
                            model: ["棋盘格", "白色", "深色"]
                            enabled: doc.viewKind === "alpha"
                            Accessible.name: "透明图预览底色"
                            onActivated: viewport.backdrop = ["checker", "white", "dark"][currentIndex]
                        }
                    }
                }
            }
            Rectangle {
                Layout.fillHeight: true
                implicitWidth: 1
                color: theme.line
            }
            Rectangle {
                id: inspector
                objectName: "inspector"
                Layout.preferredWidth: 288
                Layout.fillHeight: true
                color: theme.surface
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 0
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 44
                        color: theme.surface
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 16
                            anchors.rightMargin: 16
                            SectionTitle {
                                text: "导出设置"
                                Layout.fillWidth: true
                            }
                            Caption {
                                text: doc.ready ? "第 " + doc.page + " 页" : ""
                            }
                        }
                    }
                    Rule {}
                    ScrollView {
                        id: properties
                        objectName: "propertyScroll"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                            width: properties.availableWidth
                            spacing: 0
                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.margins: 16
                                spacing: 12
                                RowLayout {
                                    Layout.fillWidth: true
                                    SectionTitle {
                                        text: "裁剪范围"
                                        Layout.fillWidth: true
                                    }
                                    Caption {
                                        text: doc.mode === "auto" ? "自动" : "手动"
                                    }
                                }
                                RowLayout {
                                    spacing: 4
                                    Layout.fillWidth: true
                                    ActionButton {
                                        Layout.fillWidth: true
                                        text: "自动紧边"
                                        selected: doc.mode === "auto"
                                        enabled: window.editing
                                        onClicked: studio.setMode("auto")
                                    }
                                    ActionButton {
                                        Layout.fillWidth: true
                                        text: "整页"
                                        enabled: window.editing
                                        onClicked: studio.setMode("full")
                                    }
                                    ActionButton {
                                        Layout.fillWidth: true
                                        text: "手动"
                                        selected: doc.mode === "manual"
                                        enabled: window.editing
                                        onClicked: studio.setMode("manual")
                                    }
                                }
                                Caption {
                                    Layout.fillWidth: true
                                    text: "拖动蓝色边框调整选区，绿色外框为最终输出范围。"
                                }
                                ActionButton {
                                    id: exact
                                    text: selected ? "收起精确坐标" : "精确坐标"
                                    glyph: "crop"
                                    quiet: true
                                    onClicked: selected = !selected
                                }
                                GridLayout {
                                    visible: exact.selected
                                    columns: 2
                                    columnSpacing: 8
                                    rowSpacing: 8
                                    Layout.fillWidth: true
                                    Repeater {
                                        model: 4
                                        TextField {
                                            required property int index
                                            Layout.fillWidth: true
                                            Layout.preferredWidth: 100
                                            implicitHeight: 32
                                            leftPadding: 36
                                            rightPadding: 6
                                            text: (doc.base[index] * 100).toFixed(3)
                                            font.pixelSize: theme.body
                                            color: theme.ink
                                            enabled: window.editing
                                            Accessible.name: ["左边界百分比", "上边界百分比", "右边界百分比", "下边界百分比"][index]
                                            Label {
                                                anchors.left: parent.left
                                                anchors.leftMargin: 8
                                                anchors.verticalCenter: parent.verticalCenter
                                                text: ["左", "上", "右", "下"][index]
                                                color: theme.secondary
                                            }
                                            background: Rectangle {
                                                color: theme.panel
                                                radius: theme.radius
                                                border.color: parent.activeFocus ? theme.accent : theme.line
                                            }
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
                            Rule {}
                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.margins: 16
                                spacing: 10
                                RowLayout {
                                    Layout.fillWidth: true
                                    SectionTitle {
                                        text: "边缘留白"
                                        Layout.fillWidth: true
                                    }
                                    ActionButton {
                                        glyph: "link"
                                        text: window.linked ? "联动" : "独立"
                                        quiet: true
                                        selected: window.linked
                                        enabled: window.editing
                                        hint: "四边使用同一百分比，或分别调整"
                                        onClicked: {
                                            window.linked = !window.linked;
                                            if (window.linked)
                                                studio.setMargin(-1, doc.margins[0]);
                                        }
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    Repeater {
                                        model: [0, 1, 2, 5]
                                        ActionButton {
                                            required property int modelData
                                            Layout.fillWidth: true
                                            objectName: "marginPreset" + modelData
                                            text: modelData + "%"
                                            selected: doc.margins.every(v => Math.abs(v - modelData) < 0.001)
                                            enabled: window.editing
                                            onClicked: studio.setMargin(-1, modelData)
                                        }
                                    }
                                }
                                Repeater {
                                    model: window.linked ? 1 : 4
                                    RowLayout {
                                        required property int index
                                        Layout.fillWidth: true
                                        spacing: 8
                                        Caption {
                                            text: window.linked ? "四边" : ["左", "上", "右", "下"][index]
                                        }
                                        ValueSlider {
                                            Layout.fillWidth: true
                                            value: doc.margins[index]
                                            enabled: window.editing
                                            Accessible.name: window.linked ? "四边扩展百分比" : ["左边扩展", "上边扩展", "右边扩展", "下边扩展"][index]
                                            onPressedChanged: {
                                                if (pressed)
                                                    studio.beginEdit();
                                                else
                                                    studio.endEdit(false);
                                            }
                                            onMoved: studio.setMargin(window.linked ? -1 : index, value)
                                        }
                                        Label {
                                            text: doc.margins[index].toFixed(1) + "%"
                                            Layout.preferredWidth: 40
                                            horizontalAlignment: Text.AlignRight
                                            color: theme.ink
                                            font.pixelSize: theme.small
                                        }
                                    }
                                }
                                Caption {
                                    Layout.fillWidth: true
                                    text: "按选区宽高计算，最多扩展到幻灯片边缘。"
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: doc.cropSize
                                    color: theme.output
                                    font.pixelSize: theme.small
                                }
                            }
                            Rule {}
                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.margins: 16
                                spacing: 12
                                SectionTitle {
                                    text: "输出文件"
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        text: "紧凑版上限"
                                        color: theme.secondary
                                        Layout.fillWidth: true
                                    }
                                    Choice {
                                        model: ["1 MB", "2.5 MB", "5 MB", "10 MB"]
                                        currentIndex: [1, 2.5, 5, 10].indexOf(doc.limit)
                                        enabled: window.editing
                                        Accessible.name: "紧凑版大小上限"
                                        onActivated: studio.setBudget([1, 2.5, 5, 10][currentIndex])
                                    }
                                }
                                Caption {
                                    Layout.fillWidth: true
                                    text: "保留完整 SVG，同时生成紧凑 PDF / SVG 和透明 PNG。紧凑版可能压缩位图。"
                                }
                                ActionButton {
                                    Layout.fillWidth: true
                                    glyph: "folder"
                                    text: "保存位置"
                                    enabled: !doc.busy
                                    onClicked: studio.chooseOutput()
                                }
                                Caption {
                                    Layout.fillWidth: true
                                    text: doc.output
                                    elide: Text.ElideMiddle
                                    wrapMode: Text.NoWrap
                                    ToolTip.visible: pathHover.hovered
                                    ToolTip.text: doc.output
                                    HoverHandler {
                                        id: pathHover
                                    }
                                }
                            }
                            Rule {
                                visible: doc.hasResult
                            }
                            ColumnLayout {
                                visible: doc.hasResult
                                Layout.fillWidth: true
                                Layout.margins: 16
                                spacing: 8
                                SectionTitle {
                                    text: "上次导出"
                                }
                                Caption {
                                    Layout.fillWidth: true
                                    text: doc.resultSummary
                                }
                                RowLayout {
                                    ActionButton {
                                        text: "验收报告"
                                        onClicked: studio.openResult("report")
                                    }
                                    ActionButton {
                                        text: "复核文件"
                                        enabled: !doc.busy
                                        onClicked: studio.verifyResult()
                                    }
                                }
                                ActionButton {
                                    Layout.fillWidth: true
                                    glyph: "folder"
                                    text: "查看输出"
                                    onClicked: studio.openResult("folder")
                                }
                            }
                        }
                    }
                    Rule {}
                    ColumnLayout {
                        objectName: "exportDock"
                        Layout.fillWidth: true
                        Layout.margins: 16
                        spacing: 8
                        Caption {
                            Layout.fillWidth: true
                            text: doc.checkStatus
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            ActionButton {
                                objectName: "checkButton"
                                text: "检查"
                                glyph: "check"
                                hint: "仅检查参数，不生成文件"
                                enabled: !doc.busy && doc.ready
                                onClicked: studio.export(true)
                            }
                            ActionButton {
                                objectName: "exportButton"
                                Layout.fillWidth: true
                                primary: true
                                glyph: "export"
                                text: doc.operation === "export" ? "取消导出" : "导出并验收"
                                enabled: doc.operation === "export" || (!doc.busy && doc.ready)
                                onClicked: doc.operation === "export" ? studio.cancel() : studio.export(false)
                            }
                        }
                    }
                }
            }
        }
        Rule {}
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 28
            color: theme.panel
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 8
                BusyIndicator {
                    running: doc.busy
                    visible: doc.busy
                    implicitWidth: 20
                    implicitHeight: 20
                }
                Label {
                    Layout.fillWidth: true
                    text: doc.status
                    elide: Text.ElideRight
                    font.pixelSize: theme.small
                    color: theme.secondary
                }
                Caption {
                    text: doc.busy ? doc.elapsed + " 秒" : "本地处理"
                }
            }
        }
    }
}
