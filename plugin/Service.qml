import QtQuick
import Quickshell.Io

Item {
    // systemd owns the daemon, so shell reloads never destroy durable delivery.
    Process {
        command: ["systemctl", "--user", "start", "abus.service"]
        running: true
        onExited: function(code) {
            if (code !== 0) console.warn("ABUS: install the user service with python3", Qt.resolvedUrl("../install.py"), "before enabling this plugin");
            else console.log("ABUS: user service started");
        }
    }
}
