// Central place for units, the RTM volume rule, and number formatting.
// Every number that reaches the screen goes through one of these.

export type Market = "dam" | "rtm";

export const REGIONS = [
  { code: "ALL", label: "All-India", nldc: "TOTAL" },
  { code: "NR", label: "North", nldc: "NR" },
  { code: "WR", label: "West", nldc: "WR" },
  { code: "SR", label: "South", nldc: "SR" },
  { code: "ER", label: "East", nldc: "ER" },
  { code: "NER", label: "Northeast", nldc: "NER" },
] as const;

export type RegionCode = (typeof REGIONS)[number]["code"];

// Each RLDC table maps to exactly one region.
export const RLDC_TABLE: Record<Exclude<RegionCode, "ALL">, string> = {
  NR: "nrldc",
  WR: "wrldc",
  SR: "srldc",
  ER: "erldc",
  NER: "nerldc",
};

const inr = new Intl.NumberFormat("en-IN");

// --- the volume rule (locked with the user) ---------------------------------
// A 15-min block of X MW delivers X/4 MWh. Applied to RTM ONLY.
// DAM MCV is shown as reported (MW), no division.
export function rtmClearedMwh(sumMcvMw: number): number {
  return sumMcvMw / 4;
}

// Daily cleared-volume figure + its unit label, per market.
export function clearedVolume(market: Market, sumMcvMw: number) {
  if (market === "rtm") {
    const mwh = rtmClearedMwh(sumMcvMw);
    return mwh >= 1000
      ? { value: mwh / 1000, unit: "GWh", text: `${(mwh / 1000).toFixed(1)} GWh` }
      : { value: mwh, unit: "MWh", text: `${Math.round(mwh)} MWh` };
  }
  // DAM: MCV shown as reported (block-average MW). Present the daily average MW.
  return { value: sumMcvMw / 96, unit: "MW", text: `${inr.format(Math.round(sumMcvMw / 96))} MW avg` };
}

// --- formatters -------------------------------------------------------------
export const fmtGW = (mw?: number | null) =>
  mw == null ? "—" : `${(mw / 1000).toFixed(1)} GW`;

export const fmtMW = (mw?: number | null) =>
  mw == null ? "—" : `${inr.format(Math.round(mw))} MW`;

export const fmtMU = (mu?: number | null) =>
  mu == null ? "—" : `${inr.format(Math.round(mu))} MU`;

export const fmtRs = (v?: number | null) =>
  v == null ? "—" : `₹${inr.format(Math.round(v))}`;

export const fmtPct = (v?: number | null) =>
  v == null ? "—" : `${v.toFixed(1)}%`;

export const fmtDate = (iso: string) =>
  new Date(iso + "T00:00:00").toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });

// Chart palette (hardcoded hex — canvas can't read CSS vars).
export const C = {
  dam: "#378ADD",
  rtm: "#D85A30",
  teal: "#1D9E75",
  tealLight: "#9FE1CB",
  blueLight: "#85B7EB",
  purple: "#534AB7",
  coal: "#5F5E5A",
  hydro: "#378ADD",
  nuclear: "#7F77DD",
  gas: "#BA7517",
  res: "#639922",
  red: "#A32D2D",
  coral: "#F0997B",
};
