import type { Metadata } from "next";
import "./globals.css";
import { FiltersProvider } from "@/components/Filters";
import TopBar from "@/components/TopBar";

export const metadata: Metadata = {
  title: "India power dashboard — markets & supply",
  description:
    "Daily electricity supply position (NLDC + regional load despatch) and IEX day-ahead/real-time market prices for India.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <FiltersProvider>
          <TopBar />
          <main className="wrap">{children}</main>
        </FiltersProvider>
      </body>
    </html>
  );
}
