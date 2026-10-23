"use client";

import { useState } from "react";
import type { MatchAnalysis } from "@/types/api";
import { kickoffSGT, leagueAccent, money, pct, signedPct, verdictClass } from "@/lib/format";

function ProbBar({ home, draw, away }: { home: number; draw: number; away: number }) {
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-[#1a2235]" aria-label="1X2 probabilities">
      <div className="bg-[#2979FF]" style={{ width: pct(home, 1) }} />
      <div className="bg-[#64748b]" style={{ width: pct(draw, 1) }} />
      <div className="bg-[#FF6D00]" style={{ width: pct(away, 1) }} />
    </div>
  );
}

export default function MatchCard({ m, minEdge }: { m: MatchAnalysis; minEdge: number }) {
  const [showAll, setShowAll] = useState(false);
  const markets = showAll ? m.markets : m.markets.filter((x) => x.edge.best_edge >= minEdge);
  const accent = leagueAccent(m.league);

  return (
    <article id={m.slug} className="rounded-xl border border-[#1e293b] bg-[#111827] p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="font-mono text-[#94a3b8]">{kickoffSGT(m.kickoff)}</span>
        <div className="flex items-center gap-2">
          <span className="rounded px-2 py-0.5 font-semibold" style={{ color: accent, background: `${accent}22` }}>
            {m.league}
          </span>
          <span
            className={`rounded border px-2 py-0.5 ${
              m.confidence === "LOW" ? "border-[#FF1744]/40 text-[#FF1744]" : "border-[#334155] text-[#94a3b8]"
            }`}
          >
            {m.confidence}
          </span>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
        <div>
          <div className="font-semibold text-[#e2e8f0]">{m.home}</div>
          <div className="text-xs text-[#64748b]">xG {m.lambda_home.toFixed(2)}</div>
        </div>
        <div className="text-center font-mono text-sm text-[#64748b]">vs</div>
        <div className="text-right">
          <div className="font-semibold text-[#e2e8f0]">{m.away}</div>
          <div className="text-xs text-[#64748b]">xG {m.lambda_away.toFixed(2)}</div>
        </div>
      </div>

      <div className="mt-3 space-y-1.5">
        <ProbBar home={m.probs.home_win} draw={m.probs.draw} away={m.probs.away_win} />
        <div className="flex justify-between font-mono text-xs">
          <span className="text-[#2979FF]">{pct(m.probs.home_win)}</span>
          <span className="text-[#94a3b8]">Draw {pct(m.probs.draw)} · BTTS {pct(m.probs.btts)}</span>
          <span className="text-[#FF6D00]">{pct(m.probs.away_win)}</span>
        </div>
      </div>

      {(m.home_absences.length > 0 || m.away_absences.length > 0) && (
        <div className="mt-3 grid grid-cols-2 gap-3 text-xs text-[#94a3b8]">
          <div>Out: {m.home_absences.join(", ") || "—"}</div>
          <div className="text-right">Out: {m.away_absences.join(", ") || "—"}</div>
        </div>
      )}

      {m.motivation.some((x) => x !== "NEUTRAL") && (
        <div className="mt-2 text-xs text-[#64748b]">
          Motivation: {m.motivation[0]} / {m.motivation[1]}
        </div>
      )}

      {m.tie && (
        <div className="mt-3 rounded-lg border border-[#2979FF]/30 bg-[#2979FF]/5 p-3 text-xs">
          <div className="font-semibold text-[#e2e8f0]">
            Tie: {m.tie.team_a} advances {pct(m.tie.advance_a)}
            {m.tie.leg1_score && (
              <span className="ml-2 font-normal text-[#94a3b8]">
                (leg 1 {m.tie.leg1_score[0]}-{m.tie.leg1_score[1]})
              </span>
            )}
          </div>
          <div className="mt-1 text-[#94a3b8]">
            Extra time {pct(m.tie.detail.win_in_extra_time ?? 0)} · penalties {pct(m.tie.detail.penalties ?? 0)}
            {m.tie.market_price_a !== null && m.tie.edge && (
              <>
                {" "}· market {m.tie.market_price_a.toFixed(3)} ·{" "}
                <span className={`rounded border px-1 ${verdictClass(m.tie.edge.verdict)}`}>
                  {m.tie.edge.verdict} {m.tie.edge.best_side} {signedPct(m.tie.edge.best_edge)}
                </span>
              </>
            )}
          </div>
        </div>
      )}

      {m.warnings.length > 0 && (
        <ul className="mt-3 space-y-0.5 text-xs text-[#FFD600]/80">
          {m.warnings.map((w) => (
            <li key={w}>⚠ {w}</li>
          ))}
        </ul>
      )}

      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs uppercase tracking-wide text-[#64748b]">
            Markets {showAll ? `(${m.markets.length})` : `with edge ≥ ${pct(minEdge)}`}
          </span>
          {m.markets.length > 0 && (
            <button
              onClick={() => setShowAll((v) => !v)}
              className="text-xs text-[#2979FF] hover:underline"
            >
              {showAll ? "Edges only" : `Show all ${m.markets.length}`}
            </button>
          )}
        </div>
        {markets.length === 0 ? (
          <p className="text-xs text-[#64748b]">
            {m.markets.length === 0 ? "No liquid Polymarket markets yet." : "No market clears the edge filter."}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-[#64748b]">
                <tr className="text-left">
                  <th className="py-1 font-normal">Bet</th>
                  <th className="py-1 font-normal text-right">Price</th>
                  <th className="py-1 font-normal text-right">Model</th>
                  <th className="py-1 font-normal text-right">Edge</th>
                  <th className="hidden py-1 font-normal text-right sm:table-cell">Liq.</th>
                  <th className="py-1 font-normal text-right">Verdict</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {markets.map((x) => (
                  <tr key={`${x.market_type}-${x.line}-${x.label}`} className="border-t border-[#1e293b]">
                    <td className="py-1.5 pr-2 font-sans text-[#e2e8f0]">
                      {x.edge.best_side === "YES" ? x.label : `NOT ${x.label}`}
                    </td>
                    <td className="py-1.5 text-right">
                      {(x.edge.best_side === "YES" ? x.price : 1 - x.price).toFixed(3)}
                    </td>
                    <td className="py-1.5 text-right">
                      {(x.edge.best_side === "YES" ? x.model_prob : 1 - x.model_prob).toFixed(3)}
                    </td>
                    <td className={`py-1.5 text-right ${x.edge.best_edge > 0 ? "text-[#00C853]" : "text-[#94a3b8]"}`}>
                      {signedPct(x.edge.best_edge)}
                    </td>
                    <td className="hidden py-1.5 text-right text-[#94a3b8] sm:table-cell">{money(x.liquidity)}</td>
                    <td className="py-1.5 text-right">
                      <span className={`rounded border px-1.5 py-0.5 font-sans ${verdictClass(x.edge.verdict)}`}>
                        {x.edge.verdict}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </article>
  );
}
