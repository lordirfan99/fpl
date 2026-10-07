from copy import deepcopy

from pre_deadline_run import validate_transfer_cash_flow


def scenario():
    # Keeping an appreciated player does not require buying them again.
    current = [{"id": 1, "cost": 100, "selling_price": 80},
               {"id": 2, "cost": 60, "selling_price": 50}]
    final = [current[0], {"id": 3, "cost": 55}]
    moves = [{"element_out": 2, "element_in": 3, "selling_price": 50, "purchase_price": 55}]
    return current, final, moves


def test_appreciated_retained_players_are_not_rebought_and_inputs_unchanged():
    current, final, moves = scenario()
    original = deepcopy((current, final, moves))
    assert validate_transfer_cash_flow(current, final, moves, 10, 5)
    assert (current, final, moves) == original
    assert validate_transfer_cash_flow(current, current, [], 0, 0)


def test_exact_zero_bank_is_legal_but_overspend_or_wrong_remaining_bank_is_not():
    current, final, moves = scenario()
    assert validate_transfer_cash_flow(current, final, moves, 5, 0)
    assert not validate_transfer_cash_flow(current, final, moves, 4, -1)
    assert not validate_transfer_cash_flow(current, final, moves, 5, 5)
    assert not validate_transfer_cash_flow(current, final, moves, None, 0)


def test_prices_must_match_current_account_and_incoming_catalog():
    current, final, moves = scenario()
    moves[0]["selling_price"] = 60
    assert not validate_transfer_cash_flow(current, final, moves, 5, 10)
    moves[0]["selling_price"] = 50
    moves[0]["purchase_price"] = 54
    assert not validate_transfer_cash_flow(current, final, moves, 5, 1)


def test_cash_cannot_be_invented_through_duplicates_missing_or_unrecorded_moves():
    current, final, moves = scenario()
    assert not validate_transfer_cash_flow(current, final, moves * 2, 10, 0)
    assert not validate_transfer_cash_flow(current, final, [], 10, 10)
    moves[0]["element_out"] = 4
    assert not validate_transfer_cash_flow(current, final, moves, 10, 5)
    current, final, moves = scenario()
    del current[1]["selling_price"]
    assert not validate_transfer_cash_flow(current, final, moves, 10, 5)
