import { supabase } from "./supabase";
import { Market, RegionCode, REGIONS, RLDC_TABLE } from "./units";

// All snapshot queries use "<= date, newest first, limit 1" so a date that
// happens to have no report (real gaps exist) falls back to the latest
// available day instead of showing nothing.

export async function latestDate(table = "nldc_regional_psp"): Promise<string> {
  const { data } = await supabase
    .from(table)
    .select("report_date")
    .order("report_date", { ascending: false })
    .limit(1)
    .maybeSingle();
  return (data?.report_date as string) ?? new Date().toISOString().slice(0, 10);
}

export type RegionalRow = {
  report_date: string;
  region: string;
  demand_met_evening_peak_mw: number | null;
  peak_shortage_mw: number | null;
  energy_met_mu: number | null;
  hydro_gen_mu: number | null;
  wind_gen_mu: number | null;
  solar_gen_mu: number | null;
  max_demand_met_mw: number | null;
  max_demand_time: string | null;
};

// All regions (incl. TOTAL) for one report day (<= chosen date).
export async function regionalSnapshot(date: string): Promise<RegionalRow[]> {
  const { data: d } = await supabase
    .from("nldc_regional_psp")
    .select("report_date")
    .lte("report_date", date)
    .order("report_date", { ascending: false })
    .limit(1)
    .maybeSingle();
  const day = (d?.report_date as string) ?? date;
  const { data } = await supabase
    .from("nldc_regional_psp")
    .select(
      "report_date,region,demand_met_evening_peak_mw,peak_shortage_mw,energy_met_mu,hydro_gen_mu,wind_gen_mu,solar_gen_mu,max_demand_met_mw,max_demand_time"
    )
    .eq("report_date", day);
  return (data ?? []) as RegionalRow[];
}

// National peak-demand trend from NLDC TOTAL rows over a date range.
export async function nationalTrend(from: string, to: string) {
  const { data } = await supabase
    .from("nldc_regional_psp")
    .select("report_date,demand_met_evening_peak_mw,energy_met_mu,peak_shortage_mw")
    .eq("region", "TOTAL")
    .gte("report_date", from)
    .lte("report_date", to)
    .order("report_date", { ascending: true });
  return data ?? [];
}

// IEX intraday: 96 blocks for one market + date.
export async function intraday(market: Market, date: string) {
  const { data } = await supabase
    .from(`iex_${market}`)
    .select("block,hour,time_block,mcp_rs_mwh,mcv_mw")
    .eq("report_date", date)
    .order("block", { ascending: true });
  return data ?? [];
}

// IEX daily aggregates for trend charts (from the materialized view).
export async function iexDaily(market: Market, from: string, to: string) {
  const { data } = await supabase
    .from("mv_iex_daily")
    .select("report_date,avg_mcp,min_mcp,max_mcp,sum_mcv_mw,blocks")
    .eq("market", market)
    .gte("report_date", from)
    .lte("report_date", to)
    .order("report_date", { ascending: true });
  return data ?? [];
}

// National snapshot built by summing the 5 RLDC regional feeds (which stay
// current 24/7). Used instead of NLDC's national row, which can't be fetched
// from a cloud IP (grid-India blocks datacenters).
export async function nationalFromRLDC(date: string) {
  const regions = await Promise.all(
    (Object.keys(RLDC_TABLE) as Exclude<RegionCode, "ALL">[]).map(async (code) => {
      const table = `${RLDC_TABLE[code]}_regional_availability`;
      const { data } = await supabase
        .from(table)
        .select(
          "report_date,evening_peak_demand_met_mw,evening_peak_shortage_mw,offpeak_demand_met_mw,day_energy_demand_met_mu"
        )
        .lte("report_date", date)
        .order("report_date", { ascending: false })
        .limit(1)
        .maybeSingle();
      return {
        code,
        label: REGIONS.find((r) => r.code === code)!.label,
        day: (data?.report_date as string) ?? null,
        demand: data?.evening_peak_demand_met_mw ?? null,
        shortage: data?.evening_peak_shortage_mw ?? null,
        offpeak: data?.offpeak_demand_met_mw ?? null,
        energy: data?.day_energy_demand_met_mu ?? null,
      };
    })
  );
  const sum = (k: "demand" | "shortage" | "offpeak" | "energy") =>
    regions.reduce((a, r) => a + (Number(r[k]) || 0), 0);
  const day = regions.map((r) => r.day).filter(Boolean).sort().reverse()[0] ?? date;
  return {
    day,
    regions,
    total: { demand: sum("demand"), shortage: sum("shortage"), offpeak: sum("offpeak"), energy: sum("energy") },
  };
}

// Regional totals / non-state entities that live in the RLDC state feeds and
// must be excluded from a "top states" ranking.
const NON_STATE_EXACT = new Set(["NR", "WR", "ER", "SR", "NER", "REGION", "TOTAL", "ALL INDIA"]);
const isState = (s: unknown) => {
  const u = String(s).toUpperCase().trim();
  if (NON_STATE_EXACT.has(u)) return false;
  return !(u.includes("ISTS") || u.includes("RAILWAY") || u.includes("BULK CONSUMER"));
};

// Top states by max demand met, merged across the 5 RLDC state feeds.
export async function stateMaxDemandRLDC(date: string, limit = 15) {
  const per = await Promise.all(
    (Object.keys(RLDC_TABLE) as Exclude<RegionCode, "ALL">[]).map(async (code) => {
      const table = `${RLDC_TABLE[code]}_state_demand`;
      const { data: d } = await supabase
        .from(table)
        .select("report_date")
        .lte("report_date", date)
        .order("report_date", { ascending: false })
        .limit(1)
        .maybeSingle();
      const day = (d?.report_date as string) ?? date;
      const { data } = await supabase
        .from(table)
        .select("state_canonical,max_demand_met_mw,shortage_at_max_demand_mw")
        .eq("report_date", day)
        .not("max_demand_met_mw", "is", null);
      const label = REGIONS.find((r) => r.code === code)!.label;
      return (data ?? []).filter((x) => isState(x.state_canonical)).map((x) => ({ ...x, region: label }));
    })
  );
  const rows = per
    .flat()
    .sort((a, b) => Number(b.max_demand_met_mw) - Number(a.max_demand_met_mw))
    .slice(0, limit);
  return { rows };
}

// Region evening-peak vs off-peak for one day, from each RLDC table.
export async function regionPeakOffpeak(date: string) {
  const rows = await Promise.all(
    (Object.keys(RLDC_TABLE) as Exclude<RegionCode, "ALL">[]).map(async (code) => {
      const table = `${RLDC_TABLE[code]}_regional_availability`;
      const { data } = await supabase
        .from(table)
        .select(
          "report_date,evening_peak_demand_met_mw,offpeak_demand_met_mw,evening_peak_shortage_mw"
        )
        .lte("report_date", date)
        .order("report_date", { ascending: false })
        .limit(1)
        .maybeSingle();
      const label = REGIONS.find((r) => r.code === code)!.label;
      return {
        code,
        label,
        peak: data?.evening_peak_demand_met_mw ?? null,
        offpeak: data?.offpeak_demand_met_mw ?? null,
        shortage: data?.evening_peak_shortage_mw ?? null,
      };
    })
  );
  return rows;
}

// Top states by max demand met on a day (national, from NLDC state table).
export async function stateMaxDemand(date: string, limit = 12) {
  const { data: d } = await supabase
    .from("nldc_state_psp")
    .select("report_date")
    .lte("report_date", date)
    .order("report_date", { ascending: false })
    .limit(1)
    .maybeSingle();
  const day = (d?.report_date as string) ?? date;
  const { data } = await supabase
    .from("nldc_state_psp")
    .select("state_canonical,region,max_demand_met_mw,shortage_during_max_demand_mw,energy_met_mu")
    .eq("report_date", day)
    .not("max_demand_met_mw", "is", null)
    .order("max_demand_met_mw", { ascending: false })
    .limit(limit);
  return { day, rows: data ?? [] };
}

// Source-wise generation by region for a day.
export async function sourcewise(date: string) {
  const { data: d } = await supabase
    .from("nldc_sourcewise_generation")
    .select("report_date")
    .lte("report_date", date)
    .order("report_date", { ascending: false })
    .limit(1)
    .maybeSingle();
  const day = (d?.report_date as string) ?? date;
  const { data } = await supabase
    .from("nldc_sourcewise_generation")
    .select("region,coal_mu,lignite_mu,hydro_mu,nuclear_mu,gas_mu,res_mu,total_mu,res_share_pct")
    .eq("report_date", day)
    .neq("region", "ALL_INDIA");
  return { day, rows: data ?? [] };
}

// Generation outage by region for a day (central/state/total).
export async function outages(date: string) {
  const { data: d } = await supabase
    .from("nldc_generation_outage")
    .select("report_date")
    .lte("report_date", date)
    .order("report_date", { ascending: false })
    .limit(1)
    .maybeSingle();
  const day = (d?.report_date as string) ?? date;
  const { data } = await supabase
    .from("nldc_generation_outage")
    .select("region,central_sector_mw,state_sector_mw,total_mw")
    .eq("report_date", day)
    .neq("region", "TOTAL");
  return { day, rows: data ?? [] };
}
