import QtQuick

Canvas {
    id: map
    property var routes: []
    property var agents: []
    onRoutesChanged: requestPaint()
    onAgentsChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        var ctx = getContext("2d");
        ctx.reset();
        var names = [], counts = {};
        function add(name, count) {
            if (names.indexOf(name) < 0)
                names.push(name);
            counts[name] = (counts[name] || 0) + count;
        }
        for (var i = 0; i < routes.length; i++) {
            add(routes[i].from, routes[i].count);
            add(routes[i].to, routes[i].count);
        }
        for (var a = 0; a < agents.length; a++)
            add(agents[a].address, 0);
        var total = names.length;
        names = names.slice(0, 10);
        var cx = width / 2, cy = height / 2;
        var rx = Math.max(30, width / 2 - 100), ry = Math.max(25, height / 2 - 30);
        for (var n = 0; n < names.length; n++) {
            var angle = (n / names.length) * Math.PI * 2 - Math.PI / 2;
            var x = cx + Math.cos(angle) * rx, y = cy + Math.sin(angle) * ry;
            var color = names[n].indexOf("herdr:") === 0 ? Theme.warning : names[n].indexOf("broadcast:") === 0 ? Theme.muted : Theme.accent;
            ctx.strokeStyle = Qt.alpha(color, 0.35);
            ctx.lineWidth = Math.min(3, 1 + counts[names[n]] / 20);
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.lineTo(x, y);
            ctx.stroke();
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(x, y, 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillStyle = Theme.foreground;
            ctx.font = "10px monospace";
            ctx.textAlign = "center";
            var label = names[n].length > 25 ? names[n].substring(0, 22) + "…" : names[n];
            ctx.fillText(label, x, y + (y > cy ? 20 : -13));
        }
        ctx.fillStyle = Theme.raised;
        ctx.beginPath();
        ctx.arc(cx, cy, 28, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = Theme.accent;
        ctx.textAlign = "center";
        ctx.font = "bold 12px sans-serif";
        ctx.fillText("ABUS", cx, cy + 4);
        if (names.length === 0) {
            ctx.font = "11px sans-serif";
            ctx.fillStyle = Theme.muted;
            ctx.fillText("No endpoints yet", cx, cy + 55);
        }
        if (total > 10) {
            ctx.font = "10px sans-serif";
            ctx.fillStyle = Theme.muted;
            ctx.textAlign = "right";
            ctx.fillText("+ " + (total - 10) + " other endpoints", width, height - 2);
        }
    }
}
