import { ReactNode } from "react";

export function Kpi({ label, value, note, tone }: {
  label: string; value: string; note?: string; tone?: "up" | "down";
}) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note && <div className={`note ${tone ?? ""}`}>{note}</div>}
    </div>
  );
}

export function Card({ title, children, right }: {
  title?: string; children: ReactNode; right?: ReactNode;
}) {
  return (
    <div className="card">
      {(title || right) && (
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          {title && <h3>{title}</h3>}
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Legend({ items }: { items: { color: string; label: string }[] }) {
  return (
    <div className="legend">
      {items.map((i) => (
        <span key={i.label}><i className="dot" style={{ background: i.color }} />{i.label}</span>
      ))}
    </div>
  );
}

export function Loading() {
  return <div className="center">Loading…</div>;
}
export function Empty({ msg = "No data for this selection" }: { msg?: string }) {
  return <div className="center">{msg}</div>;
}
