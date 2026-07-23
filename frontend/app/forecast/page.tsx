"use client";
import { useEffect, useMemo, useState } from "react";
import { Kpi, Card, Legend, Loading, Empty } from "@/components/ui";
import { LineChart } from "@/components/charts";
import { forecastSeries } from "@/lib/queries";
import { fmtRs, C } from "@/lib/units";

const BAND = "rgba(83, 74, 183, 0.18)";

export default function ForecastPage() {
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<Awaited<ReturnType<typeof forecastSeries>>>([]);

  useEffect(() => {
    forecastSeries(90).then((r) => { setRows(r); setLoading(false); });
  }, []);

  const stats = useMemo(() => {
    const scored = rows.filter((r) => r.actual != null);
    const last30 = scored.slice(-30);
    const mae = (xs: typeof scored) =>
      xs.reduce((a, r) => a + Math.abs(r.p50 - (r.actual as number)), 0) / (xs.length || 1);
    const mape = (xs: typeof scored) =>
      (xs.reduce((a, r) => a + Math.abs((r.p50 - (r.actual as number)) / (r.actual as number)), 0) /
        (xs.length || 1)) * 100;
    const cov = (xs: typeof scored) =>
      (xs.filter((r) => (r.actual as number) >= r.p10 && (r.actual as number) <= r.p90).length /
        (xs.length || 1)) * 100;
    const next = rows.filter((r) => r.actual == null).slice(-1)[0] ?? null;
    const lastScored = scored.slice(-1)[0] ?? null;
    return { next, lastScored, mae30: mae(last30), mape30: mape(last30), cov30: cov(last30), n: scored.length };
  }, [rows]);

  if (loading) return <Loading />;
  if (!rows.length) return <Empty msg="No forecasts yet" />;

  return (
    <>
      <div className="page-head">
        <div><h1>DAM price forecast</h1>
          <div className="sub">Day-ahead daily-average MCP · ensemble (LightGBM + XGBoost + AutoETS) on demand, outages, generation mix &amp; weather · retrained daily</div></div>
        <span className="badge">Model: ens-v1</span>
      </div>

      <div className="grid kpis mb">
        <Kpi label={stats.next ? `Forecast · ${stats.next.date}` : "Next forecast"}
          value={stats.next ? fmtRs(stats.next.p50) : "—"}
          note={stats.next ? `range ${fmtRs(stats.next.p10)} – ${fmtRs(stats.next.p90)}` : "runs daily after data update"} />
        <Kpi label={stats.lastScored ? `Last scored · ${stats.lastScored.date}` : "Last scored"}
          value={stats.lastScored ? fmtRs(stats.lastScored.p50) : "—"}
          note={stats.lastScored ? `actual ${fmtRs(stats.lastScored.actual)}` : undefined}
          tone={stats.lastScored && Math.abs(stats.lastScored.p50 - (stats.lastScored.actual as number)) / (stats.lastScored.actual as number) < 0.08 ? "up" : undefined} />
        <Kpi label="Error · last 30 days" value={`${stats.mape30.toFixed(1)}%`} note={`MAE ${fmtRs(stats.mae30)} per MWh`} />
        <Kpi label="P10–P90 hit rate · 30d" value={`${stats.cov30.toFixed(0)}%`} note="target ~80%" />
      </div>

      <Card title="Forecast vs actual — last 90 days">
        <Legend items={[
          { color: C.purple, label: "Forecast P50" },
          { color: C.dam, label: "Actual DAM avg" },
          { color: "#B0AAE8", label: "P10–P90 band" },
        ]} />
        <LineChart height={320}
          data={{
            labels: rows.map((r) => r.date.slice(5)),
            datasets: [
              { label: "P90", data: rows.map((r) => r.p90), borderColor: "transparent", backgroundColor: BAND, pointRadius: 0, fill: "+1", tension: 0.3 } as any,
              { label: "P10", data: rows.map((r) => r.p10), borderColor: "transparent", backgroundColor: BAND, pointRadius: 0, fill: false, tension: 0.3 } as any,
              { label: "Forecast", data: rows.map((r) => r.p50), borderColor: C.purple, backgroundColor: C.purple, pointRadius: 0, borderWidth: 2, tension: 0.3 },
              { label: "Actual", data: rows.map((r) => r.actual), borderColor: C.dam, backgroundColor: C.dam, pointRadius: 0, borderWidth: 2, borderDash: [5, 4], tension: 0.3 },
            ],
          }}
          options={{
            plugins: { tooltip: { callbacks: {} } },
            scales: { y: { ticks: { callback: (v: any) => "₹" + (Number(v) / 1000) + "k" } }, x: { ticks: { maxTicksLimit: 10 } } },
          }} />
      </Card>

      <div style={{ height: 12 }} />
      <p className="muted" style={{ fontSize: 12.5 }}>
        Walk-forward backtest (2023 → present, 1,299 days): 8.2% mean daily error vs 13.4% for a
        naive yesterday's-price rule; monthly averages within 2.2%. History shown before today is
        the backtest; new points are produced live each day before the auction. Forecasts are not
        trading advice.
      </p>
    </>
  );
}
