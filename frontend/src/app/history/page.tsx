"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import Header from "@/components/Header";
import { api } from "@/lib/api";
import { kickoffSGT, leagueAccent, pct, verdictClass } from "@/lib/format";
import type { GradedMatch, HistoryResponse, HistorySummary } from "@/types/api";

const TABS = ["ALL", "EPL", "LALIGA", "UCL"] as const;
const TAB_LABEL: Record<(typeof TABS)[number], string> = {
  ALL: "All",
  EPL: "Premier League",
  LALIGA: "La Liga",
  UCL: "Champions League",
};

function SummaryCard({ title, s, accent }: { title: string; s?: HistorySummary; accent: string }) {
  if (!s || s.matches === 0) {
    return (
      <div className="rounded-xl border border-[#1e293b] bg-[#111827] p-4">
        <div className="text-xs font-semibold" style={{ color: accent }}>{title}</div>
        <div className="mt-2 text-sm text-[#64748b]">No graded matches yet</div>
      </div>
    );
  }
  const modelBetter = s.ll_market != null && s.ll_model != null && s.ll_model < s.ll_market;
  return (
    <div className="rounded-xl border border-[#1e293b] bg-[#111827] p-4 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold" style={{ color: accent }}>{title}</span>
        <span className="text-xs text-[#64748b]">
          {s.matches} matches · {s.live ?? 0} live
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <div className="text-xs text-[#64748b]">Pick hit rate</div>
          <div className="font-mono">
            {pct(s.pick_accuracy ?? 0)}
            {s.market_pick_accuracy != null && (
              <span className="text-xs text-[#64748b]"> vs mkt {pct(s.market_pick_accuracy)}</span>
            )}
          </div>
        </div>
        <div>
          <div className="text-xs text-[#64748b]">1X2 log loss</div>
          <div className="font-mono">
            <span className={modelBetter ? "text-[#00C853]" : ""}>{s.ll_model?.toFixed(3)}</span>
            {s.ll_market != null && (
              <span className="text-xs text-[#64748b]"> vs mkt {s.ll_market.toFixed(3)}</span>
            )}
          </div>
        </div>
        <div className="col-span-2">
          <div className="text-xs text-[#64748b]">Edge picks (flat 1 unit)</div>
          <div className="font-mono">
            {s.picks_won}/{s.picks} won ·{" "}
            <span className={(s.pnl ?? 0) >= 0 ? "text-[#00C853]" : "text-[#FF1744]"}>
              {(s.pnl ?? 0) >= 0 ? "+" : ""}
              {(s.pnl ?? 0).toFixed(2)}u ({((s.roi ?? 0) * 100).toFixed(1)}%)
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

function ProbCells({ p, highlight, result }: {
  p: { home_win: number; draw: number; away_win: number } | null;
  highlight?: "H" | "D" | "A";
  result: "H" | "D" | "A";
}) {
  if (!p) return <span className="text-[#64748b]">—</span>;
  const vals: ["H" | "D" | "A", number][] = [["H", p.home_win], ["D", p.draw], ["A", p.away_win]];
  return (
    <span className="font-mono whitespace-nowrap">
      {vals.map(([k, v], i) => (
        <span key={k}>
          {i > 0 && <span className="text-[#334155]"> / </span>}
          <span
            className={
              k === result
                ? "text-[#e2e8f0] font-semibold underline decoration-[#00C853] underline-offset-4"
                : k === highlight
                  ? "text-[#94a3b8]"
                  : "text-[#64748b]"
            }
          >
            {Math.round(v * 100)}
          </span>
        </span>
      ))}
    </span>
  );
}

function MatchRow({ g }: { g: GradedMatch }) {
  const [open, setOpen] = useState(false);
  const pnl = g.quotes.reduce((n, q) => n + q.pnl, 0);
  return (
    <Fragment>
      <tr className="border-t border-[#1e293b] align-top">
        <td className="py-2 pr-2 font-mono text-xs text-[#94a3b8] whitespace-nowrap">{kickoffSGT(g.kickoff)}</td>
        <td className="py-2 pr-2">
          <div className="flex flex-wrap items-center gap-x-2">
            <span className="text-xs font-semibold" style={{ color: leagueAccent(g.league) }}>{g.league}</span>
            <span>
              {g.home} <span className="font-mono font-semibold">{g.home_goals}-{g.away_goals}</span> {g.away}
            </span>
          </div>
          <span
            className={`mt-1 inline-block rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
              g.source === "live" ? "bg-[#00C853]/15 text-[#00C853]" : "bg-[#64748b]/15 text-[#94a3b8]"
            }`}
            title={
              g.source === "live"
                ? `Stored before kick-off${g.predicted_at ? ` (${new Date(g.predicted_at).toLocaleString()})` : ""}`
                : "Reconstructed: ratings from matches before kick-off, Polymarket price 60 min before"
            }
          >
            {g.source}
          </span>
        </td>
        <td className="py-2 pr-2 text-xs"><ProbCells p={g.probs} highlight={g.pick} result={g.result} /></td>
        <td className="py-2 pr-2 text-xs"><ProbCells p={g.market} result={g.result} /></td>
        <td className="py-2 pr-2 text-center whitespace-nowrap">
          <span className={g.pick_correct ? "text-[#00C853]" : "text-[#FF1744]"}>
            {g.pick} {g.pick_correct ? "✓" : "✗"}
          </span>
        </td>
        <td className="py-2 pr-2 text-right font-mono text-xs whitespace-nowrap">
          <span className={g.ll_market != null && g.ll_model < g.ll_market ? "text-[#00C853]" : ""}>
            {g.ll_model.toFixed(2)}
          </span>
          <span className="text-[#64748b]"> / {g.ll_market != null ? g.ll_market.toFixed(2) : "—"}</span>
        </td>
        <td className="py-2 text-right whitespace-nowrap">
          {g.quotes.length === 0 ? (
            <span className="text-xs text-[#64748b]">none</span>
          ) : (
            <button onClick={() => setOpen((v) => !v)} className="font-mono text-xs hover:underline">
              <span className={pnl >= 0 ? "text-[#00C853]" : "text-[#FF1744]"}>
                {pnl >= 0 ? "+" : ""}
                {pnl.toFixed(2)}u
              </span>{" "}
              <span className="text-[#64748b]">({g.quotes.length}) {open ? "▲" : "▼"}</span>
            </button>
          )}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7} className="pb-3">
            <div className="rounded-lg border border-[#1e293b] bg-[#0a0e17] p-3">
              <table className="w-full text-xs">
                <thead className="text-[#64748b]">
                  <tr className="text-left">
                    <th className="py-1 font-normal">Bet</th>
                    <th className="py-1 font-normal text-right">Price</th>
                    <th className="py-1 font-normal text-right">Model</th>
                    <th className="py-1 font-normal text-right">Edge</th>
                    <th className="py-1 font-normal text-right">Verdict</th>
                    <th className="py-1 font-normal text-right">Result</th>
                    <th className="py-1 font-normal text-right">P&amp;L</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {g.quotes.map((q) => (
                    <tr key={q.label} className="border-t border-[#1e293b]">
                      <td className="py-1 font-sans">{q.label}</td>
                      <td className="py-1 text-right">{q.price.toFixed(3)}</td>
                      <td className="py-1 text-right">{q.model_prob.toFixed(3)}</td>
                      <td className="py-1 text-right">+{(q.edge * 100).toFixed(1)}%</td>
                      <td className="py-1 text-right">
                        <span className={`rounded border px-1 font-sans ${verdictClass(q.verdict)}`}>{q.verdict}</span>
                      </td>
                      <td
                        className={`py-1 text-right ${
                          q.result === "WON" ? "text-[#00C853]" : q.result === "LOST" ? "text-[#FF1744]" : "text-[#94a3b8]"
                        }`}
                      >
                        {q.result}
                      </td>
                      <td className="py-1 text-right">
                        {q.pnl >= 0 ? "+" : ""}
                        {q.pnl.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export default function HistoryPage() {
  const [data, setData] = useState<HistoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(7);
  const [tab, setTab] = useState<(typeof TABS)[number]>("ALL");

  const load = useCallback(async (d: number, force = false) => {
    try {
      setData(await api.getHistory(d, force));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => load(days), 0);
    return () => clearTimeout(t);
  }, [days, load]);

  const matches = useMemo(
    () => (data?.matches ?? []).filter((m) => tab === "ALL" || m.league === tab),
    [data, tab],
  );

  return (
    <>
      <Header />
      <main className="mx-auto max-w-7xl px-4 sm:px-6 py-6 space-y-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Prediction history</h2>
            <p className="text-sm text-[#94a3b8]">
              Every played match graded against its pre-match prediction. <span className="text-[#00C853]">Live</span> =
              stored before kick-off; <span className="text-[#94a3b8]">backfill</span> = reconstructed walk-forward
              (no look-ahead) with Polymarket&apos;s price 60 min before.
            </p>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <label className="flex items-center gap-2 text-[#94a3b8]">
              Last
              <select
                value={days}
                onChange={(e) => {
                  setLoading(true);
                  setDays(Number(e.target.value));
                }}
                className="rounded border border-[#334155] bg-[#111827] px-2 py-1 text-[#e2e8f0]"
              >
                {[3, 7, 14, 30].map((d) => (
                  <option key={d} value={d}>{d} days</option>
                ))}
              </select>
            </label>
            <button
              onClick={() => {
                setLoading(true);
                load(days, true);
              }}
              className="rounded-lg border border-[#334155] px-3 py-1.5 hover:bg-[#1f2937]"
            >
              Re-grade
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-[#FF1744]/40 bg-[#FF1744]/10 px-4 py-3 text-sm text-[#FF1744]">
            {error}
          </div>
        )}

        {loading && !data ? (
          <div className="rounded-xl border border-[#1e293b] bg-[#111827] p-10 text-center text-[#94a3b8]">
            Grading matches… pulling results and historic Polymarket prices (up to a minute the first time).
          </div>
        ) : data ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <SummaryCard title="ALL" s={data.summary.ALL} accent="#00C853" />
              <SummaryCard title="PREMIER LEAGUE" s={data.summary.EPL} accent={leagueAccent("EPL")} />
              <SummaryCard title="LA LIGA" s={data.summary.LALIGA} accent={leagueAccent("LALIGA")} />
              <SummaryCard title="CHAMPIONS LEAGUE" s={data.summary.UCL} accent={leagueAccent("UCL")} />
            </div>
            <p className="text-xs text-[#64748b]">
              Small samples swing wildly: a few days of results say almost nothing about whether the model has an
              edge. See the Backtest page for the long-run numbers. Edge picks = markets where the model beat the price
              by ≥ {pct(data.pick_edge)}; several picks on one match are correlated.
            </p>

            <div className="flex flex-wrap gap-2">
              {TABS.map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`rounded-lg px-3 py-1.5 text-sm border ${
                    tab === t
                      ? "border-[#334155] bg-[#1a2235] text-[#e2e8f0]"
                      : "border-transparent text-[#94a3b8] hover:text-[#e2e8f0]"
                  }`}
                >
                  {TAB_LABEL[t]}
                  <span className="ml-1.5 font-mono text-xs text-[#64748b]">
                    {t === "ALL" ? data.matches.length : data.matches.filter((m) => m.league === t).length}
                  </span>
                </button>
              ))}
            </div>

            <section className="rounded-xl border border-[#1e293b] bg-[#111827] p-4 sm:p-5 overflow-x-auto">
              {matches.length === 0 ? (
                <p className="text-sm text-[#94a3b8]">No played matches in this window.</p>
              ) : (
                <table className="w-full min-w-[760px] text-sm">
                  <thead className="text-xs text-[#64748b]">
                    <tr className="text-left">
                      <th className="py-1.5 font-normal">Kick-off</th>
                      <th className="py-1.5 font-normal">Match</th>
                      <th className="py-1.5 font-normal">Model H/D/A %</th>
                      <th className="py-1.5 font-normal">Market H/D/A %</th>
                      <th className="py-1.5 font-normal text-center">Pick</th>
                      <th className="py-1.5 font-normal text-right">Log loss</th>
                      <th className="py-1.5 font-normal text-right">Picks P&amp;L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {matches.map((g) => (
                      <MatchRow key={g.slug} g={g} />
                    ))}
                  </tbody>
                </table>
              )}
            </section>
            <p className="text-xs text-[#64748b]">
              Underlined number = what actually happened. Graded {new Date(data.generated_at).toLocaleString()}.
            </p>
          </>
        ) : null}
      </main>
    </>
  );
}
