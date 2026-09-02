

def test_pid_accepts_bare_int_player_ids_in_transfers():
    """MILP transfer rows carry bare ids; starters/captain carry dicts. Both hash."""
    from proposal_binding import canonical_plan_hash
    plan = {
        "gw": 3,
        "transfers": [{"element_out": 427, "element_in": 154}],
        "target_starters": [{"id": 1}, {"id": 2}],
        "bench": [{"id": 3}],
        "captain": {"id": 1}, "vice": {"id": 2},
    }
    h = canonical_plan_hash(plan)
    assert isinstance(h, str) and len(h) == 64
    # stable + order-independent
    plan["transfers"] = [{"element_in": 154, "element_out": 427}]
    assert canonical_plan_hash(plan) == h
