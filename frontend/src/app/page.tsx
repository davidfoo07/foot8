"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Header from "@/components/Header";
import MatchCard from "@/components/MatchCard";
import { api } from "@/lib/api";
import { kickoffSGT, leagueAccent, pct, signedPct, verdictClass } from "@/lib/format";
import type { Dashboard as DashboardData } from "@/types/api";

const TABS = ["ALL", "EPL", "LALIGA", "UCL"] as const;
const TAB_LABEL: Record<(typeof TABS)[number], string> = {
  ALL: "All",
  EPL: "Premier League",
  LALIGA: "La Liga",
  UCL: "Champions League",
};
const REFRESH_MS = 5 * 60 * 1000;

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab] = useState<(typeof TABS)[number]>("ALL");
  const [days, setDays] = useState(7);
  const [minEdge, setMinEdge] = useState(0.03);

  const load = useCallback(async (d: number) => {
    try {
      setData(await api.getDashboard(d));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const first = setTimeout(() => load(days), 0);
    const t = setInterval(() => load(days), REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [days, load]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await api.refresh(days);
      await load(days);
    } finally {
      setRefreshing(false);
    }
  };

  const matches = useMemo(() => {
    if (!data) return [];
    return data.leagues
      .filter((l) => tab === "ALL" || l.league === tab)
      .flatMap((l) => l.matches)
      .sort((a, b) => a.kickoff.localeCompare(b.kickoff));
  }, [data, tab]);

  const warnings = data?.leagues.flatMap((l) => l.warnings) ?? [];

  return (
    <>
      <Header />
      <main className="mx-auto max-w-7xl px-4 sm:px-6 py-6 space-y-6">
        <div className="rounded-lg border border-[#FFD600]/30 bg-[#FFD600]/5 px-4 py-3 text-sm text-[#e2e8f0]">
          <strong className="text-[#FFD600]">Read before betting:</strong>{" "}
          the backtest found no profitable edge
          against Polymarket. An hour before kick-off, its prices are as sharp as Pinnacle&apos;s close. Every
          verdict is capped at <span className="font-mono">{data?.policy.max_verdict ?? "LEAN"}</span>. Treat this as a
          sanity check, not a bet list.{" "}
          <a href="/backtest" className="text-[#2979FF] hover:underline">See the numbers →</a>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2">
            {TABS.map((t) => {
              const count =
                t === "ALL"
                  ? data?.leagues.reduce((n, l) => n + l.matches.length, 0)
                  : data?.leagues.find((l) => l.league === t)?.matches.length;
              return (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`rounded-lg px-3 py-1.5 text-sm border transition-colors ${
                    tab === t
                      ? "border-[#334155] bg-[#1a2235] text-[#e2e8f0]"
                      : "border-transparent text-[#94a3b8] hover:text-[#e2e8f0]"
                  }`}
                >
                  {TAB_LABEL[t]}
                  {count !== undefined && <span className="ml-1.5 font-mono text-xs text-[#64748b]">{count}</span>}
                </button>
              );
            })}
          </div>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-2 text-[#94a3b8]">
              Days
              <select
                value={days}
                onChange={(e) => {
                  setLoading(true);
                  setDays(Number(e.target.value));
                }}
                className="rounded border border-[#334155] bg-[#111827] px-2 py-1 text-[#e2e8f0]"
              >
                {[2, 3, 7, 14].map((d) => (
                  <option key={d} value={d}>{d}</option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2 text-[#94a3b8]">
              Min edge
              <select
                value={minEdge}
                onChange={(e) => setMinEdge(Number(e.target.value))}
                className="rounded border border-[#334155] bg-[#111827] px-2 py-1 text-[#e2e8f0]"
              >
                {[0, 0.03, 0.05, 0.08].map((x) => (
                  <option key={x} value={x}>{pct(x)}</option>
                ))}
              </select>
            </label>
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="rounded-lg border border-[#334155] px-3 py-1.5 text-[#e2e8f0] hover:bg-[#1f2937] disabled:opacity-50"
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-[#FF1744]/40 bg-[#FF1744]/10 px-4 py-3 text-sm text-[#FF1744]">
            {error}. Is the backend running on port 8000?
          </div>
        )}
        {warnings.map((w) => (
          <div key={w} className="rounded-lg border border-[#FF1744]/30 px-4 py-2 text-sm text-[#FF1744]">{w}</div>
        ))}

        {loading && !data ? (
          <div className="rounded-xl border border-[#1e293b] bg-[#111827] p-10 text-center text-[#94a3b8]">
            Loading fixtures, ratings and Polymarket prices… the first load takes up to a minute.
          </div>
        ) : (
          <>
            {data && data.top_edges.length > 0 && tab === "ALL" && (
              <section className="rounded-xl border border-[#1e293b] bg-[#111827] p-4 sm:p-5">
                <h2 className="mb-3 text-sm font-semibold text-[#e2e8f0]">Largest model disagreements</h2>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-xs">
                    <thead className="text-[#64748b]">
                      <tr className="text-left">
                        <th className="py-1 font-normal">Kick-off</th>
                        <th className="py-1 font-normal">Match</th>
                        <th className="py-1 font-normal">Bet</th>
                        <th className="py-1 font-normal text-right">Price</th>
                        <th className="py-1 font-normal text-right">Model</th>
                        <th className="py-1 font-normal text-right">Edge</th>
                        <th className="py-1 font-normal text-right">Verdict</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.top_edges.map((e) => (
                        <tr key={`${e.slug}-${e.label}`} className="border-t border-[#1e293b]">
                          <td className="py-1.5 font-mono text-[#94a3b8]">{kickoffSGT(e.kickoff)}</td>
                          <td className="py-1.5">
                            <a href={`#${e.slug}`} className="hover:underline">
                              <span className="mr-1.5 font-semibold" style={{ color: leagueAccent(e.league) }}>
                                {e.league}
                              </span>
                              {e.match}
                            </a>
                          </td>
                          <td className="py-1.5">{e.side === "YES" ? e.label : `NOT ${e.label}`}</td>
                          <td className="py-1.5 text-right font-mono">
                            {(e.side === "YES" ? e.price : 1 - e.price).toFixed(3)}
                          </td>
                          <td className="py-1.5 text-right font-mono">
                            {(e.side === "YES" ? e.model_prob : 1 - e.model_prob).toFixed(3)}
                          </td>
                          <td className="py-1.5 text-right font-mono text-[#00C853]">{signedPct(e.edge)}</td>
                          <td className="py-1.5 text-right">
                            <span className={`rounded border px-1.5 py-0.5 ${verdictClass(e.verdict)}`}>
                              {e.verdict}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {matches.length === 0 ? (
              <div className="rounded-xl border border-[#1e293b] bg-[#111827] p-10 text-center text-[#94a3b8]">
                No Polymarket fixtures in the next {days} days.
              </div>
            ) : (
              <div className="grid gap-4 lg:grid-cols-2">
                {matches.map((m) => (
                  <MatchCard key={m.slug} m={m} minEdge={minEdge} />
                ))}
              </div>
            )}
            {data && (
              <p className="text-xs text-[#64748b]">
                Updated {new Date(data.generated_at).toLocaleTimeString()} · markets under $
                {data.policy.min_liquidity.toLocaleString()} liquidity hidden · prices are Polymarket mid/last.
              </p>
            )}
          </>
        )}
      </main>
    </>
  );
}
