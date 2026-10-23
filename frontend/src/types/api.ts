/** Types mirroring the Foot8 FastAPI responses. */

export interface EdgeResult {
  yes_edge: number;
  no_edge: number;
  yes_ev: number;
  no_ev: number;
  best_side: "YES" | "NO";
  best_edge: number;
  verdict: string;
  kelly_fraction: number;
  suggested_bet_pct: number;
}

export interface PricedMarket {
  market_type: string;
  line: number | null;
  label: string;
  price: number;
  model_prob: number;
  edge: EdgeResult;
  liquidity: number;
}

export interface TiePricing {
  team_a: string;
  team_b: string;
  leg1_score: [number, number] | null;
  advance_a: number;
  detail: Record<string, number>;
  market_price_a: number | null;
  edge: EdgeResult | null;
}

export interface MatchAnalysis {
  league: string;
  slug: string;
  kickoff: string;
  home: string;
  away: string;
  lambda_home: number;
  lambda_away: number;
  probs: { home_win: number; draw: number; away_win: number; btts: number };
  home_absences: string[];
  away_absences: string[];
  motivation: [string, string];
  confidence: string;
  markets: PricedMarket[];
  warnings: string[];
  tie?: TiePricing | null;
}

export interface LeagueResult {
  league: string;
  name: string;
  generated_at: string;
  compute_seconds: number;
  matches: MatchAnalysis[];
  warnings: string[];
}

export interface TopEdge {
  league: string;
  slug: string;
  kickoff: string;
  match: string;
  label: string;
  side: "YES" | "NO";
  price: number;
  model_prob: number;
  edge: number;
  verdict: string;
  liquidity: number;
}

export interface Dashboard {
  generated_at: string;
  days: number;
  leagues: LeagueResult[];
  top_edges: TopEdge[];
  policy: { max_verdict: string; min_liquidity: number };
}

export interface BacktestRow {
  name: string;
  ll_1x2: number;
  ll_ou25?: number;
}

export interface UclSplit {
  matches: number;
  ll_model: number;
  ll_uniform_prior: number;
  ou_ll_model: number;
  goals_projected: number;
  goals_actual: number;
  home_model: number;
  home_rate: number;
  with_polymarket?: number;
  ll_model_on_poly_set?: number;
  ll_polymarket?: number;
}

export interface UclBacktest {
  as_of: string;
  description: string;
  train: UclSplit;
  test: UclSplit;
  betting: Record<string, { bets: number; pnl: number; roi: number }>;
  betting_ci95?: Record<string, [number, number]>;
  conclusion?: string;
}

export interface BacktestSummary {
  as_of: string;
  conclusion: string;
  vs_pinnacle: { description: string; matches: number; rows: BacktestRow[] };
  vs_polymarket: {
    description: string;
    matches: number;
    rows: BacktestRow[];
    betting: { strategy: string; roi_range: string }[];
  };
  ucl?: UclBacktest;
}
