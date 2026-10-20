import QtQuick
import Quickshell
import Quickshell.Io

Item {
    id: root
    property var snapshot: ({status: {}, agents: [], herdr: [], messages: [], inbox: {messages: [], more: false}, activity: {more: false}, traffic: [], routes: []})
    property string selectedAgent: ""
    property bool includeAcked: false
    property int inboxAfter: 0
    property int activityBefore: 0
    property string error: ""
    property bool connected: false
    property var machines: []
    property string networkError: ""
    property var exposure: ({enabled: false})
    readonly property bool busy: poll.running
    readonly property string cli: Quickshell.shellDir + "/../bin/omarchy-abus"
    function refresh() {
        if (!poll.running) {
            poll.command = ["python3", cli, "rpc", "dashboard", JSON.stringify({name: selectedAgent || null, include_acked: includeAcked, inbox_after: inboxAfter, activity_before: activityBefore})];
            poll.running = true;
        }
    }
    function network() {
        if (!networkPoll.running) networkPoll.running = true;
        if (!statusPoll.running) statusPoll.running = true;
    }
    onSelectedAgentChanged: refresh()
    onIncludeAckedChanged: refresh()
    onInboxAfterChanged: refresh()
    onActivityBeforeChanged: refresh()
    Component.onCompleted: { refresh(); network(); }
    Timer { interval: 2000; running: true; repeat: true; onTriggered: root.refresh() }
    Timer { interval: 30000; running: true; repeat: true; onTriggered: root.network() }
    Process {
        id: poll
        stdout: StdioCollector {
            onStreamFinished: {
                try { root.snapshot = JSON.parse(text); root.connected = true; root.error = ""; }
                catch (e) { root.connected = false; }
            }
        }
        stderr: StdioCollector {
            onStreamFinished: {
                if (text.trim()) { root.connected = false; try { root.error = JSON.parse(text).error.message; } catch(e) { root.error = text.trim(); } }
            }
        }
    }
    Process {
        id: networkPoll
        command: ["python3", root.cli, "machines"]
        stdout: StdioCollector { onStreamFinished: { try { var data = JSON.parse(text); root.machines = data.machines; root.networkError = data.error || ""; } catch(e) {} } }
        stderr: StdioCollector { onStreamFinished: { if (text.trim()) root.networkError = "Tailnet discovery unavailable"; } }
    }
    Process {
        id: statusPoll
        command: ["python3", root.cli, "status"]
        stdout: StdioCollector { onStreamFinished: { try { root.exposure = JSON.parse(text).exposure; } catch(e) {} } }
    }
}
