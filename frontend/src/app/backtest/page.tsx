"use client";

import { useEffect, useState } from "react";
import Header from "@/components/Header";
import { api } from "@/lib/api";
import type { BacktestRow, BacktestSummary, UclBacktest } from "@/types/api";

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

function UclSection({ ucl }: { ucl: UclBacktest }) {
  const t = ucl.test;
  const splits = [
    { name: "Tuning season (2024-25)", s: ucl.train },
    { name: "Test (2025-26 →)", s: ucl.test },
  ];
  return (
    <section className="rounded-xl border border-[#1e293b] bg-[#111827] p-5 space-y-4">
      <h3 className="font-semibold">Champions League (cross-league model)</h3>
      <p className="text-xs text-[#94a3b8]">{ucl.description}</p>
      {ucl.conclusion && <p className="text-sm text-[#e2e8f0]">{ucl.conclusion}</p>}
      <table className="w-full text-sm">
        <thead className="text-xs text-[#64748b]">
          <tr className="text-left">
            <th className="py-1.5 font-normal">Split</th>
            <th className="py-1.5 font-normal text-right">Matches</th>
            <th className="py-1.5 font-normal text-right">1X2 log loss</th>
            <th className="py-1.5 font-normal text-right">Base rates</th>
            <th className="py-1.5 font-normal text-right">Goals proj. / actual</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {splits.map(({ name, s }) => (
            <tr key={name} className="border-t border-[#1e293b]">
              <td className="py-2 font-sans">{name}</td>
              <td className="py-2 text-right">{s.matches}</td>
              <td className="py-2 text-right">{s.ll_model.toFixed(4)}</td>
              <td className="py-2 text-right text-[#94a3b8]">{s.ll_uniform_prior.toFixed(4)}</td>
              <td className="py-2 text-right">
                {s.goals_projected.toFixed(2)} / {s.goals_actual.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {t.ll_polymarket !== undefined && t.ll_model_on_poly_set !== undefined && (
        <Table
          rows={[
            { name: `foot8 model (${t.with_polymarket} matches)`, ll_1x2: t.ll_model_on_poly_set },
            { name: "Polymarket T-60 min", ll_1x2: t.ll_polymarket },
          ]}
          showOu={false}
        />
      )}
      <ul className="text-xs text-[#94a3b8] space-y-1">
        {Object.entries(ucl.betting).map(([edge, b]) => (
          <li key={edge}>
            Buy model edges ≥{Math.round(Number(edge) * 100)}%: {b.bets} bets,{" "}
            <span className={`font-mono ${b.roi >= 0 ? "text-[#00C853]" : "text-[#FF1744]"}`}>
              {b.roi >= 0 ? "+" : ""}
              {(b.roi * 100).toFixed(1)}%
            </span>{" "}
            ROI (1¢ slippage)
            {ucl.betting_ci95?.[edge] && (
              <span className="text-[#64748b]">
                {" "}· 95% CI {(ucl.betting_ci95[edge][0] * 100).toFixed(0)}% to{" "}
                {(ucl.betting_ci95[edge][1] * 100).toFixed(0)}%
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="text-xs text-[#64748b]">
        Base rates = log loss of always predicting 45% home / 25% draw / 30% away. As of {ucl.as_of}.
      </p>
    </section>
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
            {data.ucl && <UclSection ucl={data.ucl} />}
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
