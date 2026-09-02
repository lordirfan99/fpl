// Shared lineup shapes for read-only model/lineup views (My Team, Model XIs,
// projection pitch). These are display types only — the dashboard never writes
// to FPL; every change is applied manually by the owner in the official app.

export interface LineupPlayer {
  id?: number;
  name?: string;
  position?: string;
  pos?: string;
  club?: number | string;
  cost?: number;
  xpts?: number;
  xpts_horizon?: number;
  status?: string;
  news?: string;
  starter?: boolean;
  role?: string;
}

export interface LineupTransfer {
  out_name?: string;
  in_name?: string;
  position?: string;
  out_pos?: string;
  in_pos?: string;
}
