import QtQuick
import Quickshell
import Quickshell.Io

Item {
    id: root

    property Process daemon: Process {
        command: ["python3", Quickshell.shellDir + "/../plugin/bin/abusd.py"]
        running: true
        onExited: (code) => {
            console.warn("abusd exited", code)
            // restart after short delay
            Qt.callLater(() => daemon.running = true)
        }
    }

    Component.onCompleted: {
        console.log("ABUS service started")
    }
}
