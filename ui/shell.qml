//@ pragma AppId syndicalt.abus
//@ pragma Env QT_QUICK_CONTROLS_STYLE=Basic
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io

ShellRoot {
    id: app
    Backend { id: backend }
    property int page: 0
    property var selectedMessage: null
    property var inboxPages: []
    property var activityPages: []
    property string search: ""
    readonly property var rawMessages: page === 1 ? (backend.snapshot.agent_name === backend.selectedAgent && backend.snapshot.inbox ? backend.snapshot.inbox.messages : []) : backend.snapshot.messages
    readonly property var messages: rawMessages.filter(function(m) { return !search || JSON.stringify(m).toLowerCase().indexOf(search.toLowerCase()) >= 0; })
    function chooseAgent(name) { inboxPages = []; backend.inboxAfter = 0; backend.selectedAgent = name; selectedMessage = null; page = 1; search = ""; }
    function overview() { page = 0; selectedMessage = null; }
    IpcHandler {
        target: "abus"
        function open(): void { window.visible = true; }
        function inbox(name: string): void { app.chooseAgent(name); window.visible = true; }
        function retained(name: string): void { backend.includeAcked = true; app.chooseAgent(name); window.visible = true; }
        function overview(): void { app.overview(); window.visible = true; }
        function activity(): void { app.page = 2; app.selectedMessage = null; }
        function connections(): void { app.page = 3; }
        function inspect(id: string): void {
            var found = app.rawMessages.filter(function(m) { return m.id === id; });
            if (found.length) app.selectedMessage = found[0];
        }
        function status(): string { return JSON.stringify({connected: backend.connected, page: app.page, selectedAgent: backend.selectedAgent, messages: app.messages.length, selectedMessage: app.selectedMessage ? app.selectedMessage.id : null, snapshot: backend.snapshot.status}); }
    }
    Connections {
        target: backend
        function onSnapshotChanged() {
            if (app.selectedMessage) {
                var updated = app.rawMessages.filter(function(m) { return m.id === app.selectedMessage.id; });
                if (updated.length) app.selectedMessage = updated[0];
            }
        }
    }
    component CopyText: TextArea {
        readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
        textFormat: TextEdit.PlainText
        color: Theme.foreground; font.family: "monospace"; font.pixelSize: 12
        background: null
    }
    component LabelText: Text {
        color: Theme.foreground; font.pixelSize: 14; textFormat: Text.PlainText; elide: Text.ElideRight
    }
    component SectionLabel: LabelText { color: Theme.muted; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1.4 }
    component Divider: Rectangle { color: Theme.raised; Layout.fillWidth: true; implicitHeight: 1 }
    component NavButton: Button {
        id: nav
        property bool current: false
        Layout.fillWidth: true
        implicitHeight: 40
        background: Rectangle { radius: 6; color: nav.current ? Theme.raised : nav.hovered ? Theme.background : "transparent" }
        contentItem: Text { text: nav.text; color: nav.current ? Theme.accent : Theme.foreground; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter; leftPadding: 12 }
    }
    component Metric: Rectangle {
        property string label
        property string value
        property color tint: Theme.foreground
        Layout.fillWidth: true; implicitHeight: 88; color: Theme.surface; radius: 9
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 16; spacing: 5
            SectionLabel { text: label }
            LabelText { text: value; color: tint; font.pixelSize: 27; font.bold: true }
        }
    }
    FloatingWindow {
        id: window
        title: "ABUS — Agent Monitor"
        implicitWidth: 1180; implicitHeight: 800; minimumSize: Qt.size(940, 680)
        color: Theme.background; visible: true
        onClosed: Qt.quit()
        Control {
            anchors.fill: parent
            background: Rectangle { color: Theme.background }
            font.family: "sans-serif"; font.pixelSize: 13
            palette.window: Theme.background; palette.windowText: Theme.foreground
            palette.base: Theme.surface; palette.alternateBase: Theme.background; palette.text: Theme.foreground
            palette.button: Theme.raised; palette.buttonText: Theme.foreground
            palette.highlight: Theme.accent; palette.highlightedText: Theme.surface
            palette.placeholderText: Theme.muted; palette.mid: Theme.raised; palette.light: Theme.raised; palette.dark: Theme.surface
            ColumnLayout {
                anchors.fill: parent; spacing: 0
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 80; color: Theme.surface
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 20; spacing: 14
                        Rectangle {
                            width: 38; height: 38; radius: 10; color: Theme.accent
                            LabelText { anchors.centerIn: parent; text: "⇄"; color: Theme.surface; font.pixelSize: 27; font.bold: true }
                        }
                        ColumnLayout {
                            spacing: 3
                            LabelText { text: "ABUS"; font.pixelSize: 21; font.bold: true; font.letterSpacing: 2 }
                            LabelText { text: "Agents and messages"; color: Theme.muted; font.pixelSize: 12 }
                        }
                        Item { Layout.fillWidth: true }
                        LabelText { text: "Read only"; font.pixelSize: 10; color: Theme.muted; font.letterSpacing: 1.5 }
                        Rectangle { width: 7; height: 7; radius: 4; color: backend.connected ? Theme.good : Theme.error }
                        LabelText { text: backend.connected ? "Bus online" : "Bus offline"; color: backend.connected ? Theme.good : Theme.error; font.pixelSize: 12 }
                    }
                }
                Rectangle {
                    visible: backend.error !== ""; Layout.fillWidth: true; implicitHeight: alertText.implicitHeight + 20; color: Theme.raised
                    LabelText { id: alertText; anchors.fill: parent; anchors.margins: 10; text: backend.error; color: Theme.error; wrapMode: Text.Wrap; elide: Text.ElideNone }
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
                    Rectangle {
                        Layout.preferredWidth: 245; Layout.fillHeight: true; color: Theme.surface
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 16; spacing: 10
                            SectionLabel { text: "Navigation"; Layout.bottomMargin: 6 }
                            NavButton { text: "Overview"; current: app.page === 0; onClicked: app.overview() }
                            NavButton { text: "Messages"; current: app.page === 2; onClicked: { app.page = 2; app.selectedMessage = null; app.search = ""; } }
                            NavButton { text: "Connections"; current: app.page === 3; onClicked: app.page = 3 }
                            Divider { Layout.topMargin: 8; Layout.bottomMargin: 8 }
                            RowLayout {
                                SectionLabel { text: "Inboxes"; Layout.fillWidth: true }
                                LabelText { text: String(backend.snapshot.agents.length); color: Theme.muted; font.pixelSize: 11 }
                            }
                            ScrollView {
                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                                ColumnLayout {
                                    width: parent.width; spacing: 5
                                    Repeater {
                                        model: backend.snapshot.agents
                                        delegate: Rectangle {
                                            required property var modelData
                                            Layout.fillWidth: true; implicitHeight: 61; radius: 6
                                            color: backend.selectedAgent === modelData.name && app.page === 1 ? Theme.raised : "transparent"
                                            RowLayout {
                                                anchors.fill: parent; anchors.margins: 8; spacing: 8
                                                Rectangle { width: 6; height: 6; radius: 3; color: modelData.online ? Theme.good : Theme.muted }
                                                ColumnLayout {
                                                    Layout.fillWidth: true; spacing: 3
                                                    LabelText { Layout.fillWidth: true; text: modelData.name; font.bold: true; font.pixelSize: 13 }
                                                    LabelText { Layout.fillWidth: true; text: modelData.capabilities.join(" · ") || (modelData.online ? "Present" : "Offline · inbox retained"); color: Theme.muted; font.pixelSize: 10 }
                                                }
                                                LabelText { text: modelData.unread ? String(modelData.unread) : ""; color: Theme.accent; font.bold: true }
                                            }
                                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: app.chooseAgent(modelData.name) }
                                        }
                                    }
                                    LabelText { visible: backend.snapshot.agents.length === 0; text: "No registered agents"; color: Theme.muted; wrapMode: Text.Wrap; elide: Text.ElideNone; font.pixelSize: 11; Layout.fillWidth: true; lineHeight: 1.5 }
                                    SectionLabel { text: "Herdr panes"; Layout.topMargin: 18 }
                                    Repeater {
                                        model: backend.snapshot.herdr
                                        delegate: ColumnLayout {
                                            required property var modelData
                                            Layout.fillWidth: true; Layout.margins: 7; spacing: 3
                                            LabelText { Layout.fillWidth: true; text: modelData.name + " · " + modelData.pane_id; font.pixelSize: 12 }
                                            LabelText { text: modelData.status; color: modelData.status === "blocked" ? Theme.warning : modelData.status === "working" ? Theme.good : Theme.muted; font.pixelSize: 10 }
                                        }
                                    }
                                    LabelText { visible: backend.snapshot.herdr.length === 0; text: "No live panes"; color: Theme.muted; font.pixelSize: 11 }
                                }
                            }
                            Divider {}
                            LabelText { text: "Refreshes every 2 seconds"; color: Theme.muted; font.pixelSize: 9; Layout.fillWidth: true }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.margins: 22; spacing: 14
                        RowLayout {
                            LabelText { Layout.fillWidth: true; text: app.page === 0 ? "Overview" : app.page === 1 ? backend.selectedAgent : app.page === 2 ? "Messages" : "Connections"; font.pixelSize: 25; font.bold: true }
                            LabelText { text: app.page === 1 && backend.snapshot.inbox ? String(backend.snapshot.inbox.total || 0) + (backend.includeAcked ? " messages" : " unacknowledged") : ""; color: Theme.muted; font.pixelSize: 12 }
                        }
                        LabelText { Layout.fillWidth: true; text: app.page === 0 ? "Agents, deliveries, and recent messages" : app.page === 1 ? "Reading does not acknowledge messages." : app.page === 2 ? "Retained messages" : "Service and network status"; color: Theme.muted; font.pixelSize: 12 }
                        ColumnLayout {
                            visible: app.page === 0; Layout.fillWidth: true; Layout.fillHeight: true; spacing: 16
                            RowLayout {
                                Layout.fillWidth: true; spacing: 12
                                Metric { label: "Online agents"; value: String(backend.snapshot.agents.filter(function(a) { return a.online; }).length); tint: Theme.good }
                                Metric { label: "Unread"; value: String(backend.snapshot.agents.reduce(function(n, a) { return n + a.unread; }, 0)); tint: Theme.accent }
                                Metric { label: "Queued"; value: String((backend.snapshot.status.messages || {}).queued || 0); tint: Theme.warning }
                                Metric { label: "Failed"; value: String((backend.snapshot.status.messages || {}).failed || 0); tint: ((backend.snapshot.status.messages || {}).failed || 0) ? Theme.error : Theme.muted }
                            }
                            Rectangle {
                                Layout.fillWidth: true; implicitHeight: 175; radius: 9; color: Theme.surface
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 16; spacing: 8
                                    RowLayout { SectionLabel { text: "Messages per minute"; Layout.fillWidth: true } LabelText { text: "Last hour"; color: Theme.muted; font.pixelSize: 10 } }
                                    TrafficChart { Layout.fillWidth: true; Layout.fillHeight: true; buckets: backend.snapshot.traffic || []; foreground: Theme.accent; muted: Theme.muted }
                                }
                            }
                            Rectangle {
                                Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 180; radius: 9; color: Theme.surface
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 16; spacing: 8
                                    RowLayout { SectionLabel { text: "Endpoints"; Layout.fillWidth: true } LabelText { text: "Latest 100 messages"; color: Theme.muted; font.pixelSize: 10 } }
                                    RouteMap { Layout.fillWidth: true; Layout.fillHeight: true; routes: backend.snapshot.routes || []; agents: backend.snapshot.agents }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                LabelText { text: String(backend.snapshot.herdr.filter(function(p) { return p.status === "blocked"; }).length) + " blocked Herdr panes"; color: Theme.warning; font.pixelSize: 11 }
                                Item { Layout.fillWidth: true }
                                LabelText { text: backend.snapshot.status.uptime !== undefined ? "Service uptime " + Math.floor(backend.snapshot.status.uptime / 60) + "m" : "Awaiting service"; color: Theme.muted; font.pixelSize: 11 }
                            }
                        }
                        RowLayout {
                            visible: app.page === 1 || app.page === 2; Layout.fillWidth: true; Layout.fillHeight: true; spacing: 14
                            ColumnLayout {
                                Layout.preferredWidth: app.selectedMessage ? 330 : 700; Layout.fillWidth: true; Layout.fillHeight: true
                                ComboBox {
                                    visible: app.page === 1; Layout.fillWidth: true
                                    model: ["Unacknowledged", "All retained messages"]
                                    currentIndex: backend.includeAcked ? 1 : 0
                                    onActivated: { app.inboxPages = []; backend.inboxAfter = 0; backend.includeAcked = currentIndex === 1; }
                                }
                                TextField { Layout.fillWidth: true; placeholderText: "Filter loaded messages…"; text: app.search; onTextEdited: app.search = text; selectByMouse: true }
                                ListView {
                                    id: messageList
                                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 8; model: app.messages
                                    delegate: Rectangle {
                                        required property var modelData
                                        width: messageList.width; height: 108; radius: 8
                                        color: app.selectedMessage && app.selectedMessage.id === modelData.id ? Theme.raised : Theme.surface
                                        ColumnLayout {
                                            anchors.fill: parent; anchors.margins: 12; spacing: 6
                                            RowLayout {
                                                LabelText { text: modelData.type.toUpperCase(); color: Theme.accent; font.bold: true; font.pixelSize: 10 }
                                                Item { Layout.fillWidth: true }
                                                LabelText { text: modelData.claimed ? "claimed" : modelData.acknowledged ? "acknowledged" : modelData.state; color: modelData.state === "failed" ? Theme.error : modelData.state === "queued" ? Theme.warning : Theme.muted; font.pixelSize: 11 }
                                            }
                                            LabelText { Layout.fillWidth: true; text: modelData.payload.text || JSON.stringify(modelData.payload); font.pixelSize: 13 }
                                            LabelText { Layout.fillWidth: true; text: modelData.from + " → " + modelData.to; color: Theme.muted; font.pixelSize: 10 }
                                            LabelText { text: new Date(modelData.ts * 1000).toLocaleString(); color: Theme.muted; font.pixelSize: 10 }
                                        }
                                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: app.selectedMessage = modelData }
                                    }
                                    LabelText { anchors.centerIn: parent; width: parent.width - 30; horizontalAlignment: Text.AlignHCenter; visible: messageList.count === 0; text: app.search ? "No matching messages" : app.page === 1 ? "No messages" : "No retained messages yet"; color: Theme.muted; wrapMode: Text.Wrap; elide: Text.ElideNone; lineHeight: 1.6 }
                                }
                                RowLayout {
                                    visible: app.page === 1; Layout.fillWidth: true
                                    Button {
                                        text: "Previous"; enabled: app.inboxPages.length > 0 && !backend.busy
                                        onClicked: { var pages = app.inboxPages.slice(); backend.inboxAfter = pages.pop(); app.inboxPages = pages; }
                                    }
                                    Item { Layout.fillWidth: true }
                                    Button {
                                        text: "Next"; enabled: backend.snapshot.inbox && backend.snapshot.inbox.more && !backend.busy
                                        onClicked: { app.inboxPages = app.inboxPages.concat([backend.inboxAfter]); backend.inboxAfter = backend.snapshot.inbox.next; }
                                    }
                                }
                                RowLayout {
                                    visible: app.page === 2; Layout.fillWidth: true
                                    Button {
                                        text: "Newer"; enabled: app.activityPages.length > 0 && !backend.busy
                                        onClicked: { var pages = app.activityPages.slice(); backend.activityBefore = pages.pop(); app.activityPages = pages; }
                                    }
                                    Item { Layout.fillWidth: true }
                                    Button {
                                        text: "Older"; enabled: backend.snapshot.activity && backend.snapshot.activity.more && !backend.busy
                                        onClicked: { app.activityPages = app.activityPages.concat([backend.activityBefore]); backend.activityBefore = backend.snapshot.activity.next; }
                                    }
                                }
                            }
                            Rectangle {
                                visible: app.selectedMessage !== null; Layout.preferredWidth: 350; Layout.fillWidth: true; Layout.fillHeight: true; radius: 8; color: Theme.surface
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 16; spacing: 12
                                    RowLayout { SectionLabel { text: "Message"; Layout.fillWidth: true } Button { text: "×"; flat: true; onClicked: app.selectedMessage = null } }
                                    LabelText { Layout.fillWidth: true; text: app.selectedMessage && app.selectedMessage.last_error ? app.selectedMessage.last_error : "Select text to copy."; color: app.selectedMessage && app.selectedMessage.last_error ? Theme.error : Theme.muted; wrapMode: Text.Wrap; elide: Text.ElideNone; font.pixelSize: 11 }
                                    ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; CopyText { width: parent.width; text: app.selectedMessage ? JSON.stringify(app.selectedMessage, null, 2) : "" } }
                                }
                            }
                        }
                        ColumnLayout {
                            visible: app.page === 3; Layout.fillWidth: true; Layout.fillHeight: true; spacing: 16
                            Rectangle {
                                Layout.fillWidth: true; implicitHeight: 170; radius: 8; color: Theme.surface
                                ColumnLayout {
                                    anchors.fill: parent; anchors.margins: 18; spacing: 9
                                    LabelText { text: backend.exposure.enabled ? "Tailnet transport enabled" : "Local transport"; font.pixelSize: 18; font.bold: true }
                                    CopyText { text: backend.exposure.url || "Tailnet exposure is disabled."; Layout.fillWidth: true }
                                    CopyText { text: backend.snapshot.status.socket || "Service unavailable"; Layout.fillWidth: true }
                                    LabelText { text: "Managed through CLI and MCP."; color: Theme.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; elide: Text.ElideNone; font.pixelSize: 12 }
                                }
                            }
                            SectionLabel { text: "ABUS machines" }
                            ListView {
                                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 8; model: backend.machines
                                delegate: Rectangle {
                                    required property var modelData
                                    width: ListView.view.width; height: 80; radius: 8; color: Theme.surface
                                    ColumnLayout { anchors.fill: parent; anchors.margins: 14; LabelText { text: modelData.name; font.bold: true } CopyText { text: modelData.url; Layout.fillWidth: true } }
                                }
                                LabelText { visible: backend.machines.length === 0; text: backend.networkError || "No other ABUS machines found"; color: Theme.muted; wrapMode: Text.Wrap; elide: Text.ElideNone; width: parent.width; lineHeight: 1.6 }
                            }
                        }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 28; color: Theme.surface
                    LabelText { anchors.left: parent.left; anchors.leftMargin: 20; anchors.verticalCenter: parent.verticalCenter; text: backend.snapshot.status.machine || "Disconnected"; font.pixelSize: 10; color: Theme.muted }
                    LabelText { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; text: "v1.0"; font.pixelSize: 10; color: Theme.muted }
                }
            }
        }
    }
}
