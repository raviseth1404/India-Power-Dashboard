"use client";
import { useEffect, useState } from "react";
import { useFilters } from "@/components/Filters";
import { Card, Legend, Loading, Empty } from "@/components/ui";
import { BarChart } from "@/components/charts";
import { sourcewise, outages } from "@/lib/queries";
import { REGIONS, C } from "@/lib/units";

const ORDER = ["NR", "WR", "SR", "ER", "NER"];
const rlabel = (code: string) => REGIONS.find((r) => r.code === code)?.label ?? code;

export default function GenerationPage() {
  const { date, ready } = useFilters();
  const [loading, setLoading] = useState(true);
  const [src, setSrc] = useState<{ day: string; rows: any[] }>({ day: date, rows: [] });
  const [out, setOut] = useState<{ day: string; rows: any[] }>({ day: date, rows: [] });

  useEffect(() => {
    if (!ready || !date) return;
    setLoading(true);
    Promise.all([sourcewise(date), outages(date)]).then(([s, o]) => {
      setSrc(s); setOut(o); setLoading(false);
    });
  }, [date, ready]);

  if (loading) return <Loading />;

  const srcRows = ORDER.map((c) => src.rows.find((r) => r.region === c)).filter(Boolean) as any[];
  const outRows = ORDER.map((c) => out.rows.find((r) => r.region === c)).filter(Boolean) as any[];
  const coal = (r: any) => (Number(r.coal_mu ?? 0) + Number(r.lignite_mu ?? 0)) || Number(r.thermal_combined_mu ?? 0);

  return (
    <>
      <div className="page-head">
        <div><h1>Generation mix &amp; outages</h1>
          <div className="sub">Source-wise energy met and generation outage, region-wise · national feed, latest available</div></div>
        <span className="badge">As of {src.day}</span>
      </div>

      <Card title="Source-wise generation by region (energy met, MU)">
        {srcRows.length ? (
          <>
            <Legend items={[
              { color: C.coal, label: "Coal/lignite" }, { color: C.hydro, label: "Hydro" },
              { color: C.nuclear, label: "Nuclear" }, { color: C.gas, label: "Gas" }, { color: C.res, label: "RES" },
            ]} />
            <BarChart height={260}
              data={{
                labels: srcRows.map((r) => rlabel(r.region)),
                datasets: [
                  { label: "Coal/lignite", data: srcRows.map(coal), backgroundColor: C.coal },
                  { label: "Hydro", data: srcRows.map((r) => Number(r.hydro_mu ?? 0)), backgroundColor: C.hydro },
                  { label: "Nuclear", data: srcRows.map((r) => Number(r.nuclear_mu ?? 0)), backgroundColor: C.nuclear },
                  { label: "Gas", data: srcRows.map((r) => Number(r.gas_mu ?? 0)), backgroundColor: C.gas },
                  { label: "RES", data: srcRows.map((r) => Number(r.res_mu ?? 0)), backgroundColor: C.res },
                ],
              }}
              options={{ scales: { x: { stacked: true }, y: { stacked: true, ticks: { callback: (v: any) => v + " MU" } } } }} />
          </>
        ) : <Empty msg="No source-wise generation for this day (available ~2018 onward)" />}
      </Card>

      <div style={{ height: 14 }} />
      <Card title="Generation outage by region (central vs state sector)">
        {outRows.length ? (
          <>
            <Legend items={[{ color: C.red, label: "Central sector" }, { color: C.coral, label: "State sector" }]} />
            <BarChart height={240}
              data={{
                labels: outRows.map((r) => rlabel(r.region)),
                datasets: [
                  { label: "Central", data: outRows.map((r) => Number(r.central_sector_mw ?? 0)), backgroundColor: C.red },
                  { label: "State", data: outRows.map((r) => Number(r.state_sector_mw ?? 0)), backgroundColor: C.coral },
                ],
              }}
              options={{ scales: { y: { ticks: { callback: (v: any) => (Number(v) / 1000).toFixed(0) + "k MW" } } } }} />
          </>
        ) : <Empty />}
      </Card>
    </>
  );
}
