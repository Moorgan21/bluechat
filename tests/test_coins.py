"""تست‌های واحد برای منطقِ سکه در db.py: deduct_coins و refund_coins."""

from sqlalchemy import select

import db


async def test_deduct_coins_success(make_user):
    user = await make_user(coins=10)

    new_balance = await db.deduct_coins(user.id, 3, "test_reason")

    assert new_balance == 7

    async with db.async_session() as session:
        refreshed = await session.get(db.User, user.id)
        assert refreshed.coins == 7

        tx = (
            await session.execute(
                select(db.CoinTransaction).where(db.CoinTransaction.user_id == user.id)
            )
        ).scalar_one()
        assert tx.amount == -3
        assert tx.reason == "test_reason"


async def test_deduct_coins_insufficient_balance_returns_none_and_does_not_change_balance(make_user):
    user = await make_user(coins=1)

    result = await db.deduct_coins(user.id, 5, "test_reason")

    assert result is None

    async with db.async_session() as session:
        refreshed = await session.get(db.User, user.id)
        assert refreshed.coins == 1  # دست‌نخورده

        count = len(
            (
                await session.execute(
                    select(db.CoinTransaction).where(db.CoinTransaction.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        assert count == 0  # هیچ تراکنشی برای تلاشِ ناموفق ثبت نشه


async def test_deduct_coins_exact_balance_is_allowed(make_user):
    """کاربر دقیقاً به‌اندازه‌ی هزینه سکه داره، باید مجاز باشه نه فقط وقتی بیشتر داره."""
    user = await make_user(coins=2)

    new_balance = await db.deduct_coins(user.id, 2, "test_reason")

    assert new_balance == 0


async def test_deduct_coins_unknown_user_returns_none():
    result = await db.deduct_coins(999_999_999_999, 1, "test_reason")
    assert result is None


async def test_refund_coins_success(make_user):
    user = await make_user(coins=5)

    new_balance = await db.refund_coins(user.id, 2, "test_refund_reason")

    assert new_balance == 7

    async with db.async_session() as session:
        tx = (
            await session.execute(
                select(db.CoinTransaction).where(db.CoinTransaction.user_id == user.id)
            )
        ).scalar_one()
        assert tx.amount == 2
        assert tx.reason == "test_refund_reason"


async def test_refund_coins_unknown_user_returns_none():
    result = await db.refund_coins(999_999_999_999, 5, "test_refund_reason")
    assert result is None


async def test_deduct_then_refund_round_trip_restores_balance(make_user):
    """شبیهِ سناریوی درخواستِ چت: کسر در لحظه‌ی ارسال، و برگشتِ کامل اگه
    رد یا منقضی بشه؛ موجودی باید دقیقاً به حالتِ اول برگرده."""
    user = await make_user(coins=10)

    balance_after_deduct = await db.deduct_coins(user.id, 2, "chat_request_cost")
    assert balance_after_deduct == 8

    balance_after_refund = await db.refund_coins(user.id, 2, "chat_request_reject_refund")
    assert balance_after_refund == 10
