import QtQuick

Canvas {
    id: chart
    property var buckets: []
    property color foreground: "#89b4fa"
    property color muted: "#9aa1b7"
    onBucketsChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onForegroundChanged: requestPaint()
    onPaint: {
        var ctx = getContext("2d");
        ctx.reset();
        var peak = Math.max(0, Math.max.apply(null, buckets));
        var max = Math.max(1, peak);
        var baseline = height - 20;
        var bar = width / Math.max(1, buckets.length);
        ctx.strokeStyle = Qt.alpha(muted, 0.15);
        ctx.beginPath();
        ctx.moveTo(0, baseline);
        ctx.lineTo(width, baseline);
        ctx.stroke();
        for (var i = 0; i < buckets.length; i++) {
            var h = buckets[i] / max * (baseline - 18);
            ctx.fillStyle = buckets[i] ? foreground : Qt.alpha(muted, 0.2);
            ctx.fillRect(i * bar + 1, baseline - Math.max(h, 2), Math.max(1, bar - 3), Math.max(h, 2));
        }
        ctx.font = "10px sans-serif";
        ctx.fillStyle = muted;
        ctx.fillText("60m ago", 0, height - 3);
        ctx.textAlign = "right";
        ctx.fillText("Now", width, height - 3);
        ctx.fillText(String(peak) + " / min peak", width, 10);
    }
}
