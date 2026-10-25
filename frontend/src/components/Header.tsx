"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/history", label: "History" },
  { href: "/backtest", label: "Backtest" },
];

export default function Header() {
  const [now, setNow] = useState("");
  const pathname = usePathname();

  useEffect(() => {
    const update = () =>
      setNow(
        new Date().toLocaleString("en-US", {
          timeZone: "Asia/Singapore",
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
          hour12: true,
        }) + " SGT",
      );
    update();
    const t = setInterval(update, 60_000);
    return () => clearInterval(t);
  }, []);

  return (
    <header className="border-b border-[#1e293b] bg-[#111827] px-4 sm:px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3 max-w-7xl mx-auto">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold tracking-tight">
            <span className="text-[#00C853]">FOOT</span>8
          </h1>
          <span className="text-xs px-2 py-0.5 rounded bg-[#00C853]/15 text-[#00C853] font-mono">v0.1</span>
        </div>
        <div className="flex items-center gap-6">
          <nav className="flex gap-4 text-sm">
            {NAV.map((l) => (
              <a
                key={l.href}
                href={l.href}
                className={`hover:text-[#00C853] transition-colors ${
                  pathname === l.href ? "text-[#e2e8f0]" : "text-[#94a3b8]"
                }`}
              >
                {l.label}
              </a>
            ))}
          </nav>
          <span className="text-xs text-[#64748b] font-mono">{now}</span>
        </div>
      </div>
    </header>
  );
}
