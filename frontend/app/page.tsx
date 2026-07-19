"use client";
import { useEffect, useState } from "react";
import { useFilters } from "@/components/Filters";
import { Kpi, Card, Legend, Loading, Empty } from "@/components/ui";
import { LineChart } from "@/components/charts";
import { regionalSnapshot, intraday, iexDaily, RegionalRow } from "@/lib/queries";
import { REGIONS, fmtGW, fmtMU, fmtMW, fmtRs, C } from "@/lib/units";

export default function Overview() {
  const { date, ready } = useFilters();
  const [loading, setLoading] = useState(true);
  const [regions, setRegions] = useState<RegionalRow[]>([]);
  const [dam, setDam] = useState<any[]>([]);
  const [rtm, setRtm] = useState<any[]>([]);
  const [mcp, setMcp] = useState<{ avg?: number; sum?: number } | null>(null);

  useEffect(() => {
    if (!ready || !date) return;
    setLoading(true);
    Promise.all([
      regionalSnapshot(date),
      intraday("dam", date),
      intraday("rtm", date),
      iexDaily("dam", date, date),
    ]).then(([reg, d, r, daily]) => {
      setRegions(reg);
      setDam(d);
      setRtm(r);
      setMcp(daily[0] ? { avg: daily[0].avg_mcp, sum: daily[0].sum_mcv_mw } : null);
      setLoading(false);
    });
  }, [date, ready]);

  if (loading) return <Loading />;
  const total = regions.find((r) => r.region === "TOTAL");
  const day = total?.report_date ?? date;
  const byRegion = REGIONS.filter((r) => r.code !== "ALL").map((r) => ({
    ...r, row: regions.find((x) => x.region === r.code),
  }));
  const labels = (dam.length ? dam : rtm).map((b) => (b.time_block ?? "").slice(0, 5));
  const shortPct = total?.demand_met_evening_peak_mw
    ? (((total.peak_shortage_mw ?? 0) / (total.demand_met_evening_peak_mw + (total.peak_shortage_mw ?? 0))) * 100).toFixed(2) + "% of demand"
    : undefined;

  return (
    <>
      <div className="page-head">
        <div><h1>National overview</h1><div className="sub">Supply position &amp; market snapshot</div></div>
        <span className="badge">Report day: {day}</span>
      </div>

      <div className="grid kpis mb">
        <Kpi label="Peak demand met" value={fmtGW(total?.demand_met_evening_peak_mw)} note="evening peak" />
        <Kpi label="Energy met" value={fmtMU(total?.energy_met_mu)} note="over the day" />
        <Kpi label="Peak shortage" value={fmtMW(total?.peak_shortage_mw)}
          tone={(total?.peak_shortage_mw ?? 0) > 0 ? "down" : "up"} note={shortPct} />
        <Kpi label="Avg DAM price" value={fmtRs(mcp?.avg)} note="per MWh" />
        <Kpi label="Solar + wind gen" value={fmtMU((total?.solar_gen_mu ?? 0) + (total?.wind_gen_mu ?? 0))} note="renewable energy met" />
      </div>

      <Card title="Region-wise demand met & shortage">
        <div className="grid strip">
          {byRegion.map((r) => (
            <div className="mini" key={r.code}>
              <div className="r">{r.label}</div>
              <div className="v">{fmtGW(r.row?.demand_met_evening_peak_mw)}</div>
              <div className="s" style={{ color: (r.row?.peak_shortage_mw ?? 0) > 0 ? "var(--danger)" : "var(--success)" }}>
                {(r.row?.peak_shortage_mw ?? 0) > 0 ? `−${fmtMW(r.row?.peak_shortage_mw)} short` : "met"}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <div style={{ height: 14 }} />
      <Card title="Intraday market price — DAM vs RTM">
        {dam.length || rtm.length ? (
          <>
            <Legend items={[{ color: C.dam, label: "DAM ₹/MWh" }, { color: C.rtm, label: "RTM ₹/MWh" }]} />
            <LineChart height={280}
              data={{
                labels,
                datasets: [
                  { label: "DAM", data: dam.map((b) => b.mcp_rs_mwh), borderColor: C.dam, backgroundColor: C.dam, tension: 0.35, pointRadius: 0, borderWidth: 2 },
                  { label: "RTM", data: rtm.map((b) => b.mcp_rs_mwh), borderColor: C.rtm, backgroundColor: C.rtm, borderDash: [5, 4], tension: 0.35, pointRadius: 0, borderWidth: 2 },
                ],
              }}
              options={{ scales: { y: { ticks: { callback: (v: any) => "₹" + Number(v).toLocaleString("en-IN") } } } }}
            />
          </>
        ) : <Empty msg="No IEX data for this day" />}
      </Card>
    </>
  );
}
