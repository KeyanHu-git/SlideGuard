import QtQuick
import QtQuick.Shapes

Item {
    id: glyph
    property string name: "document"
    property color ink: "#62646f"
    implicitWidth: 16
    implicitHeight: 16
    readonly property var paths: ({
            document: "M4 1.5H9L12.5 5V14.5H4Z M9 1.5V5H12.5 M6 8H10 M6 11H10",
            folder: "M1.5 5V3.5H6L8 5H14.5V13H1.5Z M1.5 7H14.5",
            crop: "M4 1V12H15 M1 4H12V15 M7 4H12V9",
            hand: "M5 8V4.5Q5 3 6.5 3V2.5Q8 1 9 3V4Q11 3 11.5 5V6Q13.5 5.5 13.5 8V10Q13 14 10 14H7Q5 14 4 12L1.5 8.5Q1 6.5 3 7L5 9 M6.5 3V7 M9 4V7 M11.5 6V8",
            undo: "M5 3L1.5 6.5L5 10 M2 6.5H9Q14 6.5 14 11V13",
            redo: "M11 3L14.5 6.5L11 10 M14 6.5H7Q2 6.5 2 11V13",
            fit: "M1.5 6V1.5H6 M10 1.5H14.5V6 M14.5 10V14.5H10 M6 14.5H1.5V10",
            minus: "M3 8H13",
            plus: "M3 8H13 M8 3V13",
            down: "M4 6L8 10L12 6",
            check: "M3 8L6.5 11.5L13 4.5",
            export: "M8 10V1.5 M4.5 5L8 1.5L11.5 5 M2 9V14H14V9",
            link: "M6 10L10 6 M6 6L8 4Q11 1 13 3Q15 5 12 8L10 10 M10 10L8 12Q5 15 3 13Q1 11 4 8L6 6"
        })
    Shape {
        anchors.centerIn: parent
        width: 16
        height: 16
        ShapePath {
            strokeColor: glyph.ink
            strokeWidth: 1.35
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg {
                path: glyph.paths[glyph.name] || glyph.paths.document
            }
        }
    }
}
