"use client";

import { useEffect, useState } from "react";
import Header from "@/components/Header";
import { api } from "@/lib/api";
import type { BacktestRow, BacktestSummary } from "@/types/api";

function Table({ rows, showOu }: { rows: BacktestRow[]; showOu: boolean }) {
  const best = Math.min(...rows.map((r) => r.ll_1x2));
  return (
    <table className="w-full text-sm">
      <thead className="text-xs text-[#64748b]">
        <tr className="text-left">
          <th className="py-1.5 font-normal">Model</th>
          <th className="py-1.5 font-normal text-right">1X2 log loss</th>
          {showOu && <th className="py-1.5 font-normal text-right">O/U 2.5 log loss</th>}
        </tr>
      </thead>
      <tbody className="font-mono">
        {rows.map((r) => (
          <tr key={r.name} className="border-t border-[#1e293b]">
            <td className="py-2 font-sans">{r.name}</td>
            <td className={`py-2 text-right ${r.ll_1x2 === best ? "text-[#00C853] font-semibold" : ""}`}>
              {r.ll_1x2.toFixed(4)}
            </td>
            {showOu && <td className="py-2 text-right">{r.ll_ou25?.toFixed(4) ?? "—"}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function BacktestPage() {
  const [data, setData] = useState<BacktestSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getBacktest().then(setData).catch((e) => setError(String(e)));
  }, []);

  return (
    <>
      <Header />
      <main className="mx-auto max-w-4xl px-4 sm:px-6 py-6 space-y-6">
        <h2 className="text-lg font-semibold">Backtest</h2>
        {error && <p className="text-sm text-[#FF1744]">{error}</p>}
        {data && (
          <>
            <p className="rounded-lg border border-[#FFD600]/30 bg-[#FFD600]/5 px-4 py-3 text-sm">{data.conclusion}</p>
            <section className="rounded-xl border border-[#1e293b] bg-[#111827] p-5 space-y-3">
              <h3 className="font-semibold">vs real Polymarket prices</h3>
              <p className="text-xs text-[#94a3b8]">{data.vs_polymarket.description}</p>
              <Table rows={data.vs_polymarket.rows} showOu={false} />
              <ul className="text-xs text-[#94a3b8] space-y-1">
                {data.vs_polymarket.betting.map((b) => (
                  <li key={b.strategy}>
                    {b.strategy}: <span className="font-mono text-[#FF1744]">{b.roi_range}</span> ROI (1¢ slippage)
                  </li>
                ))}
              </ul>
            </section>
            <section className="rounded-xl border border-[#1e293b] bg-[#111827] p-5 space-y-3">
              <h3 className="font-semibold">vs Pinnacle closing odds</h3>
              <p className="text-xs text-[#94a3b8]">{data.vs_pinnacle.description} ({data.vs_pinnacle.matches} matches)</p>
              <Table rows={data.vs_pinnacle.rows} showOu />
            </section>
            <p className="text-xs text-[#64748b]">
              Lower log loss = better probabilities. As of {data.as_of}. Re-run with{" "}
              <code className="font-mono">python scripts/polymarket_backtest.py</code>.
            </p>
          </>
        )}
      </main>
    </>
  );
}
