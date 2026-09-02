"""Telegram execution must stay explicit, hash-bound and fail closed."""
import os
from unittest.mock import patch

import telegram_bot as bot


def test_execution_requires_runtime_opt_in_and_exact_hash():
    plan = {"plan_id": "a" * 64}
    env = {bot.EXECUTION_ENABLED_ENV: "1", bot.DRY_RUN_ENV: "0"}
    with patch.dict(os.environ, env, clear=False), \
         patch.object(bot, "authorized", return_value=True), \
         patch.object(bot, "load_pending", return_value=plan), \
         patch.object(bot, "acquire_approve_lock", return_value=True), \
         patch.object(bot, "release_approve_lock"), \
         patch.object(bot, "_approve_plan_locked", return_value="executed") as execute:
        assert "Confirmation is missing" in bot.approve_plan(123)
        assert "Confirmation is missing" in bot.approve_plan(123, "b" * 64)
        assert bot.approve_plan(123, "a" * 64) == "executed"
        execute.assert_called_once()


def test_execution_is_disabled_by_default_and_in_dry_run():
    with patch.dict(os.environ, {}, clear=True):
        assert not bot.execution_enabled()
    with patch.dict(os.environ, {bot.EXECUTION_ENABLED_ENV: "1", bot.DRY_RUN_ENV: "1"}, clear=True):
        assert not bot.execution_enabled()


def test_confirmation_is_bound_to_the_canonical_plan_hash():
    plan = {
        "status": "pending", "model_version": "competitive-v4.0",
        "plan_id": "f" * 64, "input_fp": "input", "competitive": {}, "gw": 3,
    }
    env = {bot.EXECUTION_ENABLED_ENV: "1", bot.DRY_RUN_ENV: "0"}
    with patch.dict(os.environ, env, clear=False), \
         patch.object(bot, "authorized", return_value=True), \
         patch.object(bot, "load_pending", return_value=plan), \
         patch.object(bot, "canonical_plan_hash", return_value=plan["plan_id"]):
        token, message = bot.execution_confirmation(123)
    assert token == bot.short_id(plan["plan_id"])
    assert "CONFIRM FPL EXECUTION" in message
