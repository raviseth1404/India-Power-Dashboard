"use client";
import { useEffect, useMemo, useState } from "react";
import { useFilters } from "@/components/Filters";
import { Kpi, Card, Legend, Loading, Empty } from "@/components/ui";
import { LineChart, BarChart } from "@/components/charts";
import { intraday, iexDaily } from "@/lib/queries";
import { Market, clearedVolume, fmtRs, C } from "@/lib/units";

type Mode = "intraday" | "daily";
const shift = (iso: string, days: number) => {
  const d = new Date(iso + "T00:00:00"); d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
};

export default function MarketPage() {
  const { date, ready } = useFilters();
  const [market, setMarket] = useState<Market | "both">("dam");
  const [mode, setMode] = useState<Mode>("intraday");
  const [from, setFrom] = useState("");
  const [loading, setLoading] = useState(true);
  const [dam, setDam] = useState<any[]>([]);
  const [rtm, setRtm] = useState<any[]>([]);
  const [daily, setDaily] = useState<any[]>([]);

  useEffect(() => { if (date && !from) setFrom(shift(date, -90)); }, [date, from]);

  useEffect(() => {
    if (!ready || !date) return;
    setLoading(true);
    if (mode === "intraday") {
      Promise.all([intraday("dam", date), intraday("rtm", date)]).then(([d, r]) => {
        setDam(d); setRtm(r); setLoading(false);
      });
    } else {
      const m: Market = market === "rtm" ? "rtm" : "dam";
      iexDaily(m, from || shift(date, -90), date).then((rows) => { setDaily(rows); setLoading(false); });
    }
  }, [date, ready, mode, market, from]);

  const primary: Market = market === "rtm" ? "rtm" : "dam";

  const stats = useMemo(() => {
    if (mode === "intraday") {
      const src = primary === "rtm" ? rtm : dam;
      if (!src.length) return null;
      const mcps = src.map((b) => Number(b.mcp_rs_mwh)).filter((n) => !isNaN(n));
      const sumMcv = src.reduce((a, b) => a + Number(b.mcv_mw ?? 0), 0);
      return { avg: mcps.reduce((a, b) => a + b, 0) / mcps.length, max: Math.max(...mcps), sumMcv };
    }
    if (!daily.length) return null;
    const avg = daily.reduce((a, b) => a + Number(b.avg_mcp), 0) / daily.length;
    const max = Math.max(...daily.map((b) => Number(b.max_mcp)));
    const sumMcv = daily.reduce((a, b) => a + Number(b.sum_mcv_mw), 0);
    return { avg, max, sumMcv };
  }, [mode, primary, dam, rtm, daily]);

  const vol = stats ? clearedVolume(primary, stats.sumMcv) : null;

  return (
    <>
      <div className="page-head">
        <div><h1>Market prices — DAM &amp; RTM</h1>
          <div className="sub">{mode === "intraday" ? `15-min blocks · ${date}` : `daily trend · ${from} → ${date}`}</div></div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <div className="seg" role="group" aria-label="Market">
            {(["dam", "rtm", "both"] as const).map((m) => (
              <button key={m} className={market === m ? "on" : ""} onClick={() => setMarket(m)}>
                {m.toUpperCase()}</button>))}
          </div>
          <div className="seg" role="group" aria-label="Granularity">
            <button className={mode === "intraday" ? "on" : ""} onClick={() => setMode("intraday")}>Intraday</button>
            <button className={mode === "daily" ? "on" : ""} onClick={() => setMode("daily")}>Daily trend</button>
          </div>
          {mode === "daily" && (
            <input className="ctrl" type="date" value={from} max={date} onChange={(e) => setFrom(e.target.value)} aria-label="From date" />
          )}
        </div>
      </div>

      <div className="grid kpis mb">
        <Kpi label={`Avg MCP · ${primary.toUpperCase()}`} value={fmtRs(stats?.avg)} note="per MWh" />
        <Kpi label="Max MCP" value={fmtRs(stats?.max)} note={mode === "intraday" ? "peak block" : "over range"} />
        <Kpi label="Cleared volume" value={vol?.text ?? "—"}
          note={primary === "rtm" ? "MCV ÷ 4 → energy" : "MCV as reported"} />
        <Kpi label={mode === "intraday" ? "Blocks" : "Days"}
          value={mode === "intraday" ? String((primary === "rtm" ? rtm : dam).length) : String(daily.length)} note="in view" />
      </div>

      {loading ? <Loading /> : (
        <Card title={mode === "intraday" ? "Intraday price & cleared volume" : "Daily average price & volume"}>
          {(mode === "intraday" ? (dam.length || rtm.length) : daily.length) ? (
            <>
              <Legend items={[
                ...(market !== "rtm" ? [{ color: C.dam, label: "DAM MCP ₹/MWh" }] : []),
                ...(market !== "dam" ? [{ color: C.rtm, label: "RTM MCP ₹/MWh" }] : []),
                { color: C.tealLight, label: primary === "rtm" ? "RTM volume MWh" : "DAM volume MW (avg block)" },
              ]} />
              {mode === "intraday" ? (
                <IntradayChart dam={dam} rtm={rtm} market={market} primary={primary} />
              ) : (
                <DailyChart rows={daily} primary={primary} />
              )}
            </>
          ) : <Empty />}
        </Card>
      )}

      <div style={{ height: 12 }} />
      <p className="muted" style={{ fontSize: 12.5 }}>
        Volume rule: stored MCV is block-average MW. RTM energy per 15-min block = MCV ÷ 4 (MWh);
        DAM cleared volume is shown as reported (MW). Applied consistently across all views.
      </p>
    </>
  );
}

function IntradayChart({ dam, rtm, market, primary }: { dam: any[]; rtm: any[]; market: string; primary: Market }) {
  const src = primary === "rtm" ? rtm : dam;
  const labels = (dam.length ? dam : rtm).map((b: any) => (b.time_block ?? "").slice(0, 5));
  const volData = src.map((b: any) => primary === "rtm" ? Number(b.mcv_mw) / 4 : Number(b.mcv_mw));
  const ds: any[] = [
    { type: "bar", label: "vol", data: volData, backgroundColor: C.tealLight, yAxisID: "y1", order: 2 },
  ];
  if (market !== "rtm") ds.push({ type: "line", label: "DAM", data: dam.map((b) => b.mcp_rs_mwh), borderColor: C.dam, tension: 0.35, pointRadius: 0, borderWidth: 2, yAxisID: "y", order: 1 });
  if (market !== "dam") ds.push({ type: "line", label: "RTM", data: rtm.map((b) => b.mcp_rs_mwh), borderColor: C.rtm, borderDash: [5, 4], tension: 0.35, pointRadius: 0, borderWidth: 2, yAxisID: "y", order: 1 });
  return (
    <BarChart height={300} data={{ labels, datasets: ds as any }}
      options={{
        scales: {
          y: { position: "left", ticks: { callback: (v: any) => "₹" + (Number(v) / 1000) + "k" } },
          y1: { position: "right", grid: { display: false }, ticks: { callback: (v: any) => Math.round(Number(v)) } },
        },
      }} />
  );
}

function DailyChart({ rows, primary }: { rows: any[]; primary: Market }) {
  const labels = rows.map((r) => r.report_date);
  const vol = rows.map((r) => primary === "rtm" ? Number(r.sum_mcv_mw) / 4 : Number(r.sum_mcv_mw) / 96);
  return (
    <BarChart height={300}
      data={{
        labels,
        datasets: [
          { type: "bar", label: "vol", data: vol, backgroundColor: C.tealLight, yAxisID: "y1", order: 2 } as any,
          { type: "line", label: "avg MCP", data: rows.map((r) => r.avg_mcp), borderColor: primary === "rtm" ? C.rtm : C.dam, tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: "y", order: 1 } as any,
        ],
      }}
      options={{
        scales: {
          x: { ticks: { maxTicksLimit: 8 } },
          y: { position: "left", ticks: { callback: (v: any) => "₹" + (Number(v) / 1000) + "k" } },
          y1: { position: "right", grid: { display: false } },
        },
      }} />
  );
}
