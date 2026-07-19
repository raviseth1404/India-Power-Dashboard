"use client";
import { useEffect, useState } from "react";
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  BarElement, Tooltip, Filler, ChartData, ChartOptions,
} from "chart.js";
import { Line, Bar } from "react-chartjs-2";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Tooltip, Filler);

function useDark() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    setDark(mq.matches);
    const h = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, []);
  return dark;
}

function baseOptions(dark: boolean, extra?: ChartOptions<any>): ChartOptions<any> {
  const grid = dark ? "rgba(255,255,255,0.08)" : "rgba(20,30,40,0.08)";
  const tick = dark ? "#9aa5b1" : "#5f6b76";
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: dark ? "#1e242c" : "#ffffff",
        titleColor: dark ? "#e8ebee" : "#1a1d21",
        bodyColor: dark ? "#9aa5b1" : "#5f6b76",
        borderColor: dark ? "rgba(255,255,255,0.14)" : "rgba(20,30,40,0.14)",
        borderWidth: 1, padding: 10, boxPadding: 4,
      },
      ...extra?.plugins,
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: tick, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
      y: { grid: { color: grid }, ticks: { color: tick }, border: { display: false } },
      ...extra?.scales,
    },
    ...extra,
  };
}

export function LineChart({ data, height = 260, options }: {
  data: ChartData<"line">; height?: number; options?: ChartOptions<"line">;
}) {
  const dark = useDark();
  return (
    <div className="chart-box" style={{ height }}>
      <Line data={data} options={baseOptions(dark, options)} />
    </div>
  );
}

export function BarChart({ data, height = 260, options }: {
  data: ChartData<"bar">; height?: number; options?: ChartOptions<"bar">;
}) {
  const dark = useDark();
  return (
    <div className="chart-box" style={{ height }}>
      <Bar data={data} options={baseOptions(dark, options)} />
    </div>
  );
}
