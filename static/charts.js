document.addEventListener("DOMContentLoaded", () => {
    const dataEl = document.getElementById("chart-data");
    if (!dataEl) return;

    const data = JSON.parse(dataEl.textContent);
    const isLight = document.documentElement.getAttribute("data-theme") === "light";

    const colorAccent = isLight ? '#3f6dff' : '#5b8cff';
    const colorGood = isLight ? '#1fa35c' : '#2fbf71';
    const colorBad = isLight ? '#d43a3a' : '#e34f4f';
    const colorText = isLight ? '#3b4360' : '#b8c1de';
    const colorGrid = isLight ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.05)';

    Chart.defaults.color = colorText;
    Chart.defaults.font.family = 'system-ui, -apple-system, sans-serif';
    Chart.defaults.plugins.legend.labels.usePointStyle = true;

    // 1. Круговая диаграмма
    new Chart(document.getElementById('chart-pie'), {
        type: 'doughnut',
        data: {
            labels: ['Верно', 'Ошибки'],
            datasets: [{
                data: [data.pie.correct, data.pie.wrong],
                backgroundColor: [colorGood, colorBad],
                borderWidth: 0,
                hoverOffset: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '70%',
            plugins: {
                legend: { position: 'bottom' }
            }
        }
    });

    // 2. Линейная диаграмма
    new Chart(document.getElementById('chart-line'), {
        type: 'line',
        data: {
            labels: data.line.labels,
            datasets: [{
                label: 'КПД (%)',
                data: data.line.data,
                borderColor: colorAccent,
                backgroundColor: colorAccent + '33',
                fill: true,
                tension: 0.3,
                pointRadius: 4,
                pointHoverRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { min: 0, max: 100, grid: { color: colorGrid } },
                x: { grid: { display: false } }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });

    // 3. Столбчатая диаграмма
    new Chart(document.getElementById('chart-bar'), {
        type: 'bar',
        data: {
            labels: data.bar.labels,
            datasets: [
                {
                    label: 'Верно',
                    data: data.bar.correct,
                    backgroundColor: colorGood,
                    borderRadius: 4
                },
                {
                    label: 'Ошибки',
                    data: data.bar.wrong,
                    backgroundColor: colorBad,
                    borderRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { stacked: false, grid: { display: false } },
                y: { beginAtZero: true, grid: { color: colorGrid } }
            },
            plugins: {
                legend: { position: 'bottom' }
            }
        }
    });
});