from app.projection_types import ExpectedMinutes, PlayerProjection
from app.transfer_optimizer import TransferContext, captain_rankings, legal_single_transfer, net_transfer_gain


def player(element: int, team: str, position: str, xpts: float) -> PlayerProjection:
    return PlayerProjection(element, str(element), team, position, xpts, 1, xpts, xpts + 2, .3, .1,
                            ExpectedMinutes(.8, .1, 80, 20, 66, .7), {}, "test")


def test_transfer_legal_rules_and_hit_cost() -> None:
    outgoing, incoming = player(1, "A", "MID", 3), player(2, "B", "MID", 8)
    assert legal_single_transfer(outgoing, incoming, [outgoing], {1: 6, 2: 7}, TransferContext(1, 1))[0]
    assert net_transfer_gain(incoming, outgoing, TransferContext(1, 0)) == 1
    assert not legal_single_transfer(outgoing, player(3, "B", "FWD", 8), [outgoing], {1: 6, 3: 6}, TransferContext(1, 1))[0]


def test_captain_ranking_only_uses_projection_fields() -> None:
    assert captain_rankings([player(1, "A", "MID", 4), player(2, "B", "MID", 7)])[0]["element"] == 2
