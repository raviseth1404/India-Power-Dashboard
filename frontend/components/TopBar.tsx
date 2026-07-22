"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useFilters } from "./Filters";
import { REGIONS, RegionCode } from "@/lib/units";

const TABS = [
  { href: "/", label: "Overview" },
  { href: "/market", label: "Market prices" },
  { href: "/supply", label: "Supply position" },
  { href: "/generation", label: "Generation" },
  { href: "/forecast", label: "Forecast" },
];

export default function TopBar() {
  const path = usePathname();
  const { date, setDate, region, setRegion } = useFilters();
  return (
    <header className="topbar">
      <div className="wrap topbar-inner">
        <div className="brand">
          India power dashboard
          <small>markets &amp; supply position</small>
        </div>
        <nav className="tabs">
          {TABS.map((t) => (
            <Link key={t.href} href={t.href} className={`tab ${path === t.href ? "active" : ""}`}>
              {t.label}
            </Link>
          ))}
        </nav>
        <div className="controls">
          <select
            className="ctrl"
            value={region}
            onChange={(e) => setRegion(e.target.value as RegionCode)}
            aria-label="Region"
          >
            {REGIONS.map((r) => (
              <option key={r.code} value={r.code}>{r.label}</option>
            ))}
          </select>
          <input
            className="ctrl"
            type="date"
            value={date}
            max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => setDate(e.target.value)}
            aria-label="Report date"
          />
        </div>
      </div>
    </header>
  );
}
