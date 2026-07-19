"use client";
import { useEffect, useState } from "react";
import { useFilters } from "@/components/Filters";
import { Card, Legend, Loading, Empty } from "@/components/ui";
import { BarChart } from "@/components/charts";
import { regionPeakOffpeak, stateMaxDemandRLDC } from "@/lib/queries";
import { fmtGW, fmtMW, C } from "@/lib/units";

export default function SupplyPage() {
  const { date, ready } = useFilters();
  const [loading, setLoading] = useState(true);
  const [po, setPo] = useState<any[]>([]);
  const [states, setStates] = useState<{ rows: any[] }>({ rows: [] });

  useEffect(() => {
    if (!ready || !date) return;
    setLoading(true);
    Promise.all([regionPeakOffpeak(date), stateMaxDemandRLDC(date, 15)]).then(([p, s]) => {
      setPo(p); setStates(s); setLoading(false);
    });
  }, [date, ready]);

  if (loading) return <Loading />;

  return (
    <>
      <div className="page-head">
        <div><h1>Supply position — regions &amp; states</h1>
          <div className="sub">Evening peak vs off-peak, and states by maximum demand met</div></div>
        <span className="badge">Latest available per region</span>
      </div>

      <Card title="Region-wise evening peak vs off-peak demand met">
        {po.some((r) => r.peak != null) ? (
          <>
            <Legend items={[{ color: C.teal, label: "Evening peak" }, { color: C.blueLight, label: "Off-peak" }]} />
            <BarChart height={230}
              data={{
                labels: po.map((r) => r.label),
                datasets: [
                  { label: "Evening peak", data: po.map((r) => (r.peak ?? 0) / 1000), backgroundColor: C.teal },
                  { label: "Off-peak", data: po.map((r) => (r.offpeak ?? 0) / 1000), backgroundColor: C.blueLight },
                ],
              }}
              options={{ scales: { y: { ticks: { callback: (v: any) => v + " GW" } } } }} />
          </>
        ) : <Empty />}
      </Card>

      <div style={{ height: 14 }} />
      <div className="grid cols-2">
        <Card title="Top states by maximum demand met">
          {states.rows.length ? (
            <BarChart height={Math.max(240, states.rows.length * 26 + 40)}
              data={{
                labels: states.rows.map((r) => r.state_canonical),
                datasets: [{ label: "Max demand met", data: states.rows.map((r) => Number(r.max_demand_met_mw) / 1000), backgroundColor: C.purple }],
              }}
              options={{ indexAxis: "y" as const, scales: { x: { ticks: { callback: (v: any) => v + " GW" } }, y: { ticks: { autoSkip: false } } } }} />
          ) : <Empty />}
        </Card>

        <Card title="State detail">
          {states.rows.length ? (
            <div className="tbl-scroll">
              <table className="tbl">
                <thead><tr>
                  <th>State</th><th>Region</th><th className="num">Max demand</th>
                  <th className="num">Shortage</th>
                </tr></thead>
                <tbody>
                  {states.rows.map((r) => (
                    <tr key={r.state_canonical}>
                      <td>{r.state_canonical}</td>
                      <td className="muted">{r.region}</td>
                      <td className="num">{fmtGW(r.max_demand_met_mw)}</td>
                      <td className="num">{Number(r.shortage_at_max_demand_mw) ? fmtMW(r.shortage_at_max_demand_mw) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <Empty />}
        </Card>
      </div>
    </>
  );
}
