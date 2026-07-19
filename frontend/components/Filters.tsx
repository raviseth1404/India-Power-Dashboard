"use client";
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { RegionCode } from "@/lib/units";

type Filters = {
  date: string;
  setDate: (d: string) => void;
  region: RegionCode;
  setRegion: (r: RegionCode) => void;
  ready: boolean;
};

const Ctx = createContext<Filters | null>(null);
export const useFilters = () => {
  const c = useContext(Ctx);
  if (!c) throw new Error("useFilters must be used within FiltersProvider");
  return c;
};

export function FiltersProvider({ children }: { children: ReactNode }) {
  // Default to today; every query falls back to the latest available day <= this,
  // so the current 24/7 feeds (RLDC + IEX) show without waiting on a lookup.
  const [date, setDate] = useState("");
  const [region, setRegion] = useState<RegionCode>("ALL");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setDate(new Date().toISOString().slice(0, 10));
    setReady(true);
  }, []);

  return (
    <Ctx.Provider value={{ date, setDate, region, setRegion, ready }}>
      {children}
    </Ctx.Provider>
  );
}
