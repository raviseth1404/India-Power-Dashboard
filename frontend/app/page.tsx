"use client";
import { useEffect, useState } from "react";
import { useFilters } from "@/components/Filters";
import { Kpi, Card, Legend, Loading, Empty } from "@/components/ui";
import { LineChart } from "@/components/charts";
import { nationalFromRLDC, intraday, iexDaily } from "@/lib/queries";
import { fmtGW, fmtMU, fmtMW, fmtRs, C } from "@/lib/units";

export default function Overview() {
  const { date, ready } = useFilters();
  const [loading, setLoading] = useState(true);
  const [nat, setNat] = useState<Awaited<ReturnType<typeof nationalFromRLDC>> | null>(null);
  const [dam, setDam] = useState<any[]>([]);
  const [rtm, setRtm] = useState<any[]>([]);
  const [dmcp, setDmcp] = useState<number | undefined>();
  const [rmcp, setRmcp] = useState<number | undefined>();

  useEffect(() => {
    if (!ready || !date) return;
    setLoading(true);
    Promise.all([
      nationalFromRLDC(date),
      intraday("dam", date),
      intraday("rtm", date),
      iexDaily("dam", date, date),
      iexDaily("rtm", date, date),
    ]).then(([n, d, r, dd, rr]) => {
      setNat(n); setDam(d); setRtm(r);
      setDmcp(dd[0]?.avg_mcp); setRmcp(rr[0]?.avg_mcp);
      setLoading(false);
    });
  }, [date, ready]);

  if (loading || !nat) return <Loading />;
  const t = nat.total;
  const labels = (dam.length ? dam : rtm).map((b) => (b.time_block ?? "").slice(0, 5));
  const shortPct = t.demand
    ? `${((t.shortage / (t.demand + t.shortage)) * 100).toFixed(2)}% of demand`
    : undefined;

  return (
    <>
      <div className="page-head">
        <div><h1>National overview</h1><div className="sub">Supply position &amp; market snapshot</div></div>
        <span className="badge">Report day: {nat.day}</span>
      </div>

      <div className="grid kpis mb">
        <Kpi label="Peak demand met" value={fmtGW(t.demand)} note="evening peak · all-India" />
        <Kpi label="Energy met" value={fmtMU(t.energy)} note="over the day" />
        <Kpi label="Peak shortage" value={fmtMW(t.shortage)}
          tone={t.shortage > 0 ? "down" : "up"} note={shortPct} />
        <Kpi label="Avg DAM price" value={fmtRs(dmcp)} note="per MWh" />
        <Kpi label="Avg RTM price" value={fmtRs(rmcp)} note="per MWh" />
      </div>

      <Card title="Region-wise demand met & shortage">
        <div className="grid strip">
          {nat.regions.map((r) => (
            <div className="mini" key={r.code}>
              <div className="r">{r.label}</div>
              <div className="v">{fmtGW(r.demand)}</div>
              <div className="s" style={{ color: (Number(r.shortage) || 0) > 0 ? "var(--danger)" : "var(--success)" }}>
                {(Number(r.shortage) || 0) > 0 ? `−${fmtMW(r.shortage)} short` : "met"}
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
