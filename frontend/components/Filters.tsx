"use client";
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { RegionCode } from "@/lib/units";
import { latestDate } from "@/lib/queries";

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
  const [date, setDate] = useState("");
  const [region, setRegion] = useState<RegionCode>("ALL");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    latestDate().then((d) => {
      setDate(d);
      setReady(true);
    });
  }, []);

  return (
    <Ctx.Provider value={{ date, setDate, region, setRegion, ready }}>
      {children}
    </Ctx.Provider>
  );
}
