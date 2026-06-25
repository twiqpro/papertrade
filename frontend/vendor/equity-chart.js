function renderEquityChart(canvas, points) {
  if (!canvas || !points.length) return;
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  const values = points.map((p) => Number(p.equity || 0));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 1);
  ctx.strokeStyle = "#4f8cff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = (index / Math.max(points.length - 1, 1)) * (width - 20) + 10;
    const y = height - 10 - ((Number(point.equity) - min) / range) * (height - 20);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = "#9aa4b2";
  ctx.font = "12px sans-serif";
  ctx.fillText(`Min ${min.toFixed(0)}`, 10, height - 2);
  ctx.fillText(`Max ${max.toFixed(0)}`, width - 80, 14);
}

function renderDrawdownChart(canvas, points) {
  if (!canvas || !points.length) return;
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  const values = points.map((p) => Number(p.drawdown || 0));
  const min = Math.min(...values);
  const max = Math.max(...values, 0);
  const range = Math.max(max - min, 1);
  ctx.strokeStyle = "#ff6b6b";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = (index / Math.max(points.length - 1, 1)) * (width - 20) + 10;
    const y = height - 10 - ((Number(point.drawdown) - min) / range) * (height - 20);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}
