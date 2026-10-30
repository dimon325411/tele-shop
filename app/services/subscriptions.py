"""Plans, trials, subscription activation and expiry."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.models import Plan, Subscription, SubscriptionStatus, User
from app.domain.plans import DEFAULT_PLANS, Access, AccessDenied, compute_new_period
from app.domain.timing import Clock, utcnow
from app.storage.repo import Repository


class SubscriptionService:
    def __init__(self, repo: Repository, clock: Clock = utcnow) -> None:
        self.repo = repo
        self.clock = clock

    async def seed_plans(self, plans: list[Plan] | None = None) -> None:
        for plan in plans or DEFAULT_PLANS:
            await self.repo.upsert_plan(plan)

    async def access_for(self, user: User) -> Access:
        now = self.clock()
        sub = await self.repo.get_active_subscription(user.id)
        plan = await self.repo.get_plan(sub.plan_id) if sub else None
        return Access(user=user, subscription=sub, plan=plan, now=now)

    async def start_trial(self, user: User) -> Subscription:
        """Give the free trial once per user. Raises AccessDenied('trial_used')."""
        async with self.repo.transaction():
            fresh = await self.repo.get_user(user.id)
            if fresh is None or fresh.trial_used:
                raise AccessDenied("trial_used")
            if await self.repo.get_active_subscription(user.id):
                raise AccessDenied("already_subscribed")
            trial = next((p for p in await self.repo.list_plans() if p.is_trial), None)
            if trial is None:
                raise AccessDenied("trial_unavailable")
            now = self.clock()
            sub = await self.repo.create_subscription(user.id, trial.id, "trial", now,
                                                      now + timedelta(days=trial.duration_days), None)
            await self.repo.update_user(user.id, trial_used=True)
            return sub

    async def activate(self, user_id: int, plan: Plan, origin: str, payment_id: int | None,
                       days_override: int | None = None) -> Subscription:
        """Activate/extend a subscription. Must be called inside a transaction by the caller
        when combined with payment bookkeeping (it opens one itself otherwise)."""
        async with self.repo.transaction():
            now = self.clock()
            current = await self.repo.get_active_subscription(user_id)
            current_plan = await self.repo.get_plan(current.plan_id) if current else None
            if days_override is not None:
                plan_for_period = Plan(**{**plan.__dict__, "duration_days": days_override})
            else:
                plan_for_period = plan
            starts, expires, _bonus = compute_new_period(now, plan_for_period, current, current_plan)
            if current is not None:
                # close the previous record; the new one carries the combined period
                await self.repo.set_subscription_status(current.id, SubscriptionStatus.EXPIRED.value,
                                                        expires_at=max(now, current.starts_at + timedelta(seconds=1)))
                await self.repo.mark_expiry_notified(current.id, now)  # replaced, not "expired" for the user
            sub = await self.repo.create_subscription(user_id, plan.id, origin, starts, expires, payment_id)
            # saved searches resume right away instead of waiting for their "no access" back-off
            for search in await self.repo.list_user_searches(user_id):
                if search.is_active and (search.next_check_at is None or search.next_check_at > now):
                    await self.repo.update_search(search.id, next_check_at=now)
            return sub

    async def revoke(self, subscription_id: int, status: SubscriptionStatus) -> None:
        now = self.clock()
        await self.repo.set_subscription_status(subscription_id, status.value)
        await self.repo.mark_expiry_notified(subscription_id, now)

    async def expire_due(self, limit: int = 500) -> list[Subscription]:
        """Mark subscriptions past their end as expired. Searches stay saved but stop running."""
        now = self.clock()
        expired = await self.repo.list_expired_active_subscriptions(now, limit)
        for sub in expired:
            await self.repo.set_subscription_status(sub.id, SubscriptionStatus.EXPIRED.value)
        return expired

    async def pop_expiry_notices(self, limit: int = 200) -> list[Subscription]:
        """Expired subscriptions whose owner was not told yet (marks them as told)."""
        now = self.clock()
        subs = await self.repo.list_unnotified_expired(limit)
        for sub in subs:
            await self.repo.mark_expiry_notified(sub.id, now)
        return subs
