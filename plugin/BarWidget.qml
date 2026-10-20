import QtQuick
import Quickshell
import Quickshell.Io

Item {
    id: root
    property var bar: null
    property var settings: ({})
    property string moduleName: "syndicalt.abus"
    property int unread: 0
    property bool online: false
    readonly property string cli: (Quickshell.env("XDG_CONFIG_HOME") || Quickshell.env("HOME") + "/.config") + "/omarchy/plugins/syndicalt.abus/bin/omarchy-abus"
    implicitWidth: label.implicitWidth + 22
    implicitHeight: bar ? bar.barSize : 32
    function open() {
        Quickshell.execDetached(["python3", cli, "ui"]);
    }
    function close() {
    }
    readonly property bool opened: false
    Text {
        id: label
        anchors.centerIn: parent
        text: "⇄" + (root.unread ? " " + root.unread : "")
        textFormat: Text.PlainText
        font.family: root.bar ? root.bar.fontFamily : "sans-serif"
        font.pixelSize: 15
        color: root.bar ? root.bar.barForeground : "#cdd6f4"
        opacity: root.online ? 1 : 0.5
    }
    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: root.open()
    }
    Process {
        id: poll
        command: ["python3", root.cli, "list"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    var data = JSON.parse(text);
                    root.unread = data.agents.reduce(function (n, a) {
                        return n + a.unread;
                    }, 0);
                    root.online = true;
                } catch (e) {
                    root.online = false;
                }
            }
        }
        onExited: function (code) {
            if (code !== 0)
                root.online = false;
        }
    }
    Timer {
        interval: 5000
        repeat: true
        running: true
        triggeredOnStart: true
        onTriggered: if (!poll.running)
            poll.running = true
    }
}
