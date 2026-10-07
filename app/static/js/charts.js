// Chart auto-initializer — reads data-chart-config from elements
// Works with HTMX: re-initializes after swaps

function initCharts(root) {
    root = root || document;

    // Chart.js (canvas elements)
    root.querySelectorAll('[data-chart-config]').forEach(function(el) {
        var existing = Chart.getChart(el);
        if (existing) existing.destroy();

        try {
            var config = JSON.parse(el.getAttribute('data-chart-config'));
            new Chart(el, config);
        } catch (e) {
            console.error('Chart init failed:', e, el.id);
        }
    });

    // ECharts (div elements)
    root.querySelectorAll('[data-echart-config]').forEach(function(el) {
        var existing = echarts.getInstanceByDom(el);
        if (existing) existing.dispose();

        try {
            var config = JSON.parse(el.getAttribute('data-echart-config'));

            // Heatmap tooltip — needs a JS function, can't be in JSON
            var heatmapScreens = el.getAttribute('data-heatmap-screens');
            if (heatmapScreens && config.tooltip) {
                var screens = JSON.parse(heatmapScreens);
                config.tooltip.formatter = function(params) {
                    var to = screens[params.data[0]] || '?';
                    var from = screens[params.data[1]] || '?';
                    var count = params.data[2];
                    return '<b>' + from + '</b> → <b>' + to + '</b><br/>' + count + ' transitions';
                };
            }

            var chart = echarts.init(el, 'dark');
            chart.setOption(config);

            // Resize on container change
            var observer = new ResizeObserver(function() { chart.resize(); });
            observer.observe(el);

            // Cleanup on HTMX swap
            el.addEventListener('htmx:beforeSwap', function() {
                observer.disconnect();
                chart.dispose();
            }, { once: true });
        } catch (e) {
            console.error('EChart init failed:', e, el.id);
        }
    });
}

// Init on page load
document.addEventListener('DOMContentLoaded', function() {
    initCharts();
});

// Re-init after HTMX swaps
document.body.addEventListener('htmx:afterSettle', function(event) {
    initCharts(event.detail.target);
});
