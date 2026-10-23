/** Formatting helpers. */

export function pct(p: number, digits = 0): string {
  return `${(p * 100).toFixed(digits)}%`;
}

export function signedPct(p: number, digits = 1): string {
  const v = (p * 100).toFixed(digits);
  return p >= 0 ? `+${v}%` : `${v}%`;
}

/** "Sat 24 Oct, 10:00 PM SGT" */
export function kickoffSGT(iso: string): string {
  return (
    new Date(iso).toLocaleString("en-SG", {
      timeZone: "Asia/Singapore",
      weekday: "short",
      day: "numeric",
      month: "short",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    }) + " SGT"
  );
}

export function money(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}k`;
  return `$${n.toFixed(0)}`;
}

export function verdictClass(verdict: string): string {
  switch (verdict) {
    case "STRONG BUY":
      return "bg-[#00C853]/15 text-[#00C853] border-[#00C853]/40";
    case "BUY":
      return "bg-[#4CAF50]/15 text-[#4CAF50] border-[#4CAF50]/40";
    case "LEAN":
      return "bg-[#FFD600]/10 text-[#FFD600] border-[#FFD600]/30";
    default:
      return "bg-[#78909C]/10 text-[#78909C] border-[#78909C]/30";
  }
}

export function leagueAccent(league: string): string {
  return { EPL: "#7C4DFF", LALIGA: "#FF6D00", UCL: "#2979FF" }[league] ?? "#94a3b8";
}
