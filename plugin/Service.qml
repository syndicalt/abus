import QtQuick
import Quickshell.Io

Item {
    readonly property string setup: decodeURIComponent(Qt.resolvedUrl("bin/abus-setup.py").toString().replace("file://", ""))
    // Setup and the daemon outlive shell reloads; unchanged setup never restarts the bus.
    Process {
        command: ["python3", setup]
        running: true
        onExited: function (code) {
            if (code !== 0)
                console.warn("ABUS setup failed; details are in journalctl --user -u abus-setup.service");
            else
                console.log("ABUS: user service ready");
        }
    }
}
