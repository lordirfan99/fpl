"""Small, deterministic legal transfer evaluator for the V5 laboratory."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .projection_types import PlayerProjection


@dataclass(frozen=True)
class TransferContext:
    bank: float
    free_transfers: int
    chip_active: bool = False


def legal_single_transfer(
    outgoing: PlayerProjection,
    incoming: PlayerProjection,
    squad: Iterable[PlayerProjection],
    prices: dict[int, float],
    context: TransferContext,
) -> tuple[bool, str]:
    if context.chip_active:
        return False, "chip_active"
    if outgoing.position != incoming.position:
        return False, "position_mismatch"
    if incoming.element == outgoing.element:
        return False, "same_player"
    if prices.get(incoming.element, 0.0) > prices.get(outgoing.element, 0.0) + context.bank:
        return False, "insufficient_budget"
    clubs = Counter(player.team for player in squad if player.element != outgoing.element)
    if clubs[incoming.team] >= 3:
        return False, "club_limit"
    return True, "legal"


def net_transfer_gain(incoming: PlayerProjection, outgoing: PlayerProjection, context: TransferContext) -> float:
    hit = 0.0 if context.free_transfers > 0 else 4.0
    return round(incoming.xpts_mean - outgoing.xpts_mean - hit, 2)


def captain_rankings(squad: Iterable[PlayerProjection]) -> list[dict[str, float | int | str]]:
    """Pure football ranking; ownership is intentionally unavailable here."""
    return [
        {"element": row.element, "name": row.name, "xpts_mean": row.xpts_mean,
         "p_10_plus": row.p_10_plus, "expected_minutes": row.expected_minutes.expected_minutes}
        for row in sorted(squad, key=lambda item: (item.xpts_mean, item.p_10_plus), reverse=True)
        if row.expected_minutes.p_start > 0
    ]
