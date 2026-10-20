pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io

Item {
    id: root
    property color background: "#1e1e2e"
    property color surface: "#171723"
    property color raised: "#353543"
    property color foreground: "#cdd6f4"
    property color muted: "#9aa1b7"
    property color accent: "#89b4fa"
    property color good: "#a6e3a1"
    property color warning: "#f9e2af"
    property color error: "#f38ba8"
    function load() {
        var raw = colors.text();
        var keys = {background: "background", dark_background: "surface", lighter_background: "raised",
                    foreground: "foreground", dark_foreground: "muted", accent: "accent",
                    green: "good", yellow: "warning", red: "error"};
        for (var key in keys) {
            var match = raw.match(new RegExp("^" + key + "\\s*=\\s*\"(#[0-9a-fA-F]{6})\"", "m"));
            if (match) root[keys[key]] = match[1];
        }
    }
    FileView {
        id: colors
        path: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/omarchy/current/theme/colors.toml"
        watchChanges: true
        onLoaded: root.load()
        onFileChanged: reload()
    }
}
