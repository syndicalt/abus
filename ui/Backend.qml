import QtQuick
import Quickshell
import Quickshell.Io

Item {
    id: root
    property var snapshot: ({
            status: {},
            agents: [],
            herdr: [],
            messages: [],
            inbox: {
                messages: [],
                more: false
            },
            activity: {
                more: false
            },
            traffic: [],
            routes: []
        })
    property string selectedAgent: ""
    property bool includeAcked: false
    property double inboxAfter: 0
    property double activityBefore: 0
    property string error: ""
    property bool connected: false
    property var machines: []
    property string networkError: ""
    property var exposure: ({
            enabled: null,
            error: "Exposure has not been inspected"
        })
    property bool refreshPending: false
    readonly property bool busy: poll.running
    readonly property string cli: Quickshell.shellDir + "/../bin/omarchy-abus"
    function refresh() {
        if (poll.running) {
            refreshPending = true;
            return;
        }
        if (!poll.running) {
            poll.command = ["python3", cli, "rpc", "dashboard", JSON.stringify({
                    name: selectedAgent || null,
                    include_acked: includeAcked,
                    inbox_after: inboxAfter,
                    activity_before: activityBefore
                })];
            poll.running = true;
        }
    }
    function errorMessage(raw) {
        try {
            var value = JSON.parse(raw);
            if (value.error && value.error.message)
                return value.error.message;
        } catch (error) {
            return raw.trim().slice(0, 1000) || String(error);
        }
        return raw.trim().slice(0, 1000);
    }
    function network() {
        if (!networkPoll.running)
            networkPoll.running = true;
        if (!statusPoll.running)
            statusPoll.running = true;
    }
    onSelectedAgentChanged: refresh()
    onIncludeAckedChanged: refresh()
    onInboxAfterChanged: refresh()
    onActivityBeforeChanged: refresh()
    Component.onCompleted: {
        refresh();
        network();
    }
    Timer {
        interval: 2000
        running: true
        repeat: true
        onTriggered: root.refresh()
    }
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: root.network()
    }
    Process {
        id: poll
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    var value = JSON.parse(text);
                    if (!value.status || value.status.service !== "abus" || !Array.isArray(value.agents) || !Array.isArray(value.messages))
                        throw new Error("Invalid ABUS dashboard response");
                    root.snapshot = value;
                    root.connected = true;
                    root.error = "";
                } catch (error) {
                    root.connected = false;
                    root.error = "Cannot read dashboard: " + String(error);
                }
            }
        }
        stderr: StdioCollector {
            onStreamFinished: {
                if (text.trim()) {
                    root.connected = false;
                    root.error = root.errorMessage(text);
                }
            }
        }
        onExited: function (code) {
            if (code !== 0) {
                root.connected = false;
                if (!root.error)
                    root.error = "Dashboard request failed";
            }
            if (root.refreshPending) {
                root.refreshPending = false;
                Qt.callLater(root.refresh);
            }
        }
    }
    Process {
        id: networkPoll
        command: ["python3", root.cli, "machines"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    var data = JSON.parse(text);
                    if (!Array.isArray(data.machines))
                        throw new Error("Invalid ABUS discovery response");
                    root.machines = data.machines;
                    root.networkError = data.error || (data.errors && data.errors.length ? "Some peers could not be inspected." : "");
                } catch (error) {
                    root.networkError = "Discovery is stale: " + String(error);
                }
            }
        }
        stderr: StdioCollector {
            onStreamFinished: {
                if (text.trim())
                    root.networkError = "Discovery is stale: " + root.errorMessage(text);
            }
        }
    }
    Process {
        id: statusPoll
        command: ["python3", root.cli, "status"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    var value = JSON.parse(text).exposure;
                    if (!value || (value.enabled !== null && typeof value.enabled !== "boolean"))
                        throw new Error("Invalid exposure response");
                    root.exposure = value;
                } catch (error) {
                    root.exposure = {
                        enabled: null,
                        error: "Exposure is unknown: " + String(error)
                    };
                }
            }
        }
        stderr: StdioCollector {
            onStreamFinished: {
                if (text.trim())
                    root.exposure = {
                        enabled: null,
                        error: root.errorMessage(text)
                    };
            }
        }
    }
}
