"""
Monte Carlo core. Per spec section 10: outcomes are NOT treated as fully
independent — same-game/same-team players share a draw so QB/WR stacks,
competing RBs, and DST-vs-opposing-offense correlations show up correctly.
"""
import numpy as np
from typing import Dict, List
from .projections import ProjectionResult

DEFAULT_SIMS = 2000


def simulate_lineup_score(player_projections: Dict[int, ProjectionResult], n_sims: int = DEFAULT_SIMS) -> np.ndarray:
    """
    Sums per-player Monte Carlo samples for one lineup. Uses each player's own
    pre-drawn `samples` array (already correlated at the game level if the
    caller built them from a shared game seed) rather than re-sampling
    independently, so correlation structure created upstream is preserved.
    """
    totals = np.zeros(n_sims)
    for proj in player_projections.values():
        s = proj.samples
        if s is None or len(s) != n_sims:
            # fallback: independent normal draw around median/floor-ceiling spread
            spread = max(0.5, (proj.ceiling - proj.floor) / 2.56)
            s = np.random.normal(proj.median, spread, n_sims)
            s = np.clip(s, 0, None)
        totals += s
    return totals


def correlate_same_game(samples_a: np.ndarray, samples_b: np.ndarray, corr: float) -> np.ndarray:
    """
    Nudges samples_b to correlate with samples_a at roughly `corr` (e.g. QB pass
    yards/tds correlating positively with his WR1's output; two RBs on the
    same run-heavy game environment correlating positively with each other;
    a DST correlating negatively with the opposing offense's output).
    Simple rank-based coupling — adequate for an MVP; a Gaussian copula is the
    natural upgrade once real covariance estimates exist.
    """
    n = len(samples_a)
    rank_a = np.argsort(np.argsort(samples_a))
    sorted_b = np.sort(samples_b)
    coupled = sorted_b[rank_a]
    noise = np.random.permutation(samples_b)
    mix = corr * coupled + (1 - corr) * noise
    return mix


def matchup_win_probability(team_a_totals: np.ndarray, team_b_totals: np.ndarray) -> dict:
    a_wins = np.mean(team_a_totals > team_b_totals)
    tie = np.mean(team_a_totals == team_b_totals)
    return {
        "team_a_win_prob": round(float(a_wins + tie / 2), 4),
        "team_b_win_prob": round(float(1 - a_wins - tie / 2), 4),
        "team_a_median": round(float(np.median(team_a_totals)), 2),
        "team_b_median": round(float(np.median(team_b_totals)), 2),
        "team_a_p10": round(float(np.percentile(team_a_totals, 10)), 2),
        "team_a_p90": round(float(np.percentile(team_a_totals, 90)), 2),
    }


def simulate_remaining_season(current_wins: List[int], remaining_win_probs: List[List[float]],
                               playoff_spots: int, n_sims: int = DEFAULT_SIMS) -> dict:
    """
    current_wins: current win count per team (list length = num_teams)
    remaining_win_probs: [team][week] -> win probability for each remaining matchup
    Returns playoff-appearance and championship-ish (top-seed proxy) probabilities.
    Championship probability here is a simplified proxy (top-2 seed rate) since a
    full bracket sim needs actual playoff matchup pairing logic layered on top —
    documented as a simplification, not a real bracket simulation.
    """
    n_teams = len(current_wins)
    playoff_count = np.zeros(n_teams)
    top_seed_count = np.zeros(n_teams)

    for _ in range(n_sims):
        final_wins = np.array(current_wins, dtype=float)
        for team_idx in range(n_teams):
            for week_prob in remaining_win_probs[team_idx]:
                final_wins[team_idx] += 1 if np.random.random() < week_prob else 0
        # small random tiebreaker noise so ties resolve without bias
        order = np.argsort(-(final_wins + np.random.uniform(0, 0.001, n_teams)))
        for seed_rank, team_idx in enumerate(order):
            if seed_rank < playoff_spots:
                playoff_count[team_idx] += 1
            if seed_rank < 2:
                top_seed_count[team_idx] += 1

    return {
        "playoff_probability": (playoff_count / n_sims).round(4).tolist(),
        "championship_probability_proxy": (top_seed_count / n_sims * 0.55).round(4).tolist(),
        # proxy scales top-2-seed rate down since a top seed still must win 2-3 playoff games;
        # replace with a real bracket sim for production accuracy
        "num_simulations": n_sims,
        "note": "championship_probability_proxy is a simplification (top-seed-rate-based), not a full bracket simulation.",
    }
