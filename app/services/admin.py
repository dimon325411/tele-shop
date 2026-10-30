"""Administrative operations. Every mutating action is written to the audit log.

Admins are identified by numeric Telegram user id (ADMIN_IDS), never by username.
"""

from __future__ import annotations

from datetime import timedelta

from app.config import Settings
from app.domain.models import SourceState
from app.domain.plans import EDITABLE_PLAN_FIELDS
from app.domain.timing import Clock, utcnow
from app.services.subscriptions import SubscriptionService
from app.storage.repo import Repository


class NotAdmin(Exception):
    pass


class AdminError(Exception):
    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


class AdminService:
    def __init__(self, repo: Repository, subscriptions: SubscriptionService, settings: Settings,
                 clock: Clock = utcnow) -> None:
        self.repo = repo
        self.subs = subscriptions
        self.settings = settings
        self.clock = clock

    def ensure_admin(self, telegram_id: int) -> None:
        if not self.settings.is_admin(telegram_id):
            raise NotAdmin()

    async def _audit(self, admin_id: int, action: str, target: str | None, details: dict | None = None) -> None:
        await self.repo.add_audit(admin_id, action, target, details or {}, self.clock())

    async def stats(self, admin_id: int) -> dict[str, int]:
        self.ensure_admin(admin_id)
        return await self.repo.stats(self.clock())

    async def sources(self, admin_id: int) -> list[SourceState]:
        self.ensure_admin(admin_id)
        return await self.repo.list_source_states()

    async def set_source_enabled(self, admin_id: int, source: str, enabled: bool, reason: str | None = None) -> None:
        self.ensure_admin(admin_id)
        now = self.clock()
        state = await self.repo.get_source_state(source) or SourceState(source=source)
        state.enabled = enabled
        state.status = "ok" if enabled else "disabled"
        state.disabled_reason = None if enabled else (reason or "admin")
        if enabled:
            state.blocked_until = None
        await self.repo.save_source_state(state, now)
        await self._audit(admin_id, "source_enable" if enabled else "source_disable", source, {"reason": reason})

    async def set_plan_field(self, admin_id: int, plan_code: str, field: str, raw_value: str) -> None:
        self.ensure_admin(admin_id)
        if field not in EDITABLE_PLAN_FIELDS:
            raise AdminError("bad_field", fields=", ".join(sorted(EDITABLE_PLAN_FIELDS)))
        kind = EDITABLE_PLAN_FIELDS[field]
        try:
            if kind is bool:
                value: object = raw_value.strip().lower() in {"1", "true", "yes", "tak", "on"}
            elif kind is int:
                value = int(raw_value)
                if value < 0:
                    raise ValueError
            elif kind is list:
                value = [s.strip() for s in raw_value.split(",") if s.strip()]
            else:
                value = raw_value.strip()
        except ValueError as exc:
            raise AdminError("bad_value") from exc
        if field == "min_interval_seconds" and int(value) < 30:  # type: ignore[arg-type]
            raise AdminError("bad_value")
        if field == "duration_days" and int(value) < 1:  # type: ignore[arg-type]
            raise AdminError("bad_value")
        if await self.repo.get_plan_by_code(plan_code) is None:
            raise AdminError("no_plan")
        await self.repo.update_plan(plan_code, **{field: value})
        await self._audit(admin_id, "plan_set", plan_code, {"field": field, "value": str(value)})

    async def set_suspended(self, admin_id: int, telegram_id: int, suspended: bool, reason: str | None) -> None:
        self.ensure_admin(admin_id)
        user = await self.repo.get_user_by_telegram_id(telegram_id)
        if user is None:
            raise AdminError("no_user")
        await self.repo.update_user(user.id, is_suspended=suspended, suspended_reason=reason if suspended else None)
        if suspended:
            for search in await self.repo.list_user_searches(user.id):
                if search.is_active:
                    await self.repo.update_search(search.id, is_active=False, paused_reason="suspended")
        await self._audit(admin_id, "suspend" if suspended else "unsuspend", str(telegram_id), {"reason": reason})

    async def grant(self, admin_id: int, telegram_id: int, plan_code: str, days: int) -> None:
        self.ensure_admin(admin_id)
        if days < 1 or days > 3650:
            raise AdminError("bad_value")
        user = await self.repo.get_user_by_telegram_id(telegram_id)
        if user is None:
            raise AdminError("no_user")
        plan = await self.repo.get_plan_by_code(plan_code)
        if plan is None:
            raise AdminError("no_plan")
        await self.subs.activate(user.id, plan, "admin", None, days_override=days)
        await self._audit(admin_id, "grant", str(telegram_id), {"plan": plan_code, "days": days})

    async def payment_errors(self, admin_id: int) -> list[dict]:
        self.ensure_admin(admin_id)
        return await self.repo.list_payment_errors(20)

    async def audit_log(self, admin_id: int) -> list[dict]:
        self.ensure_admin(admin_id)
        return await self.repo.list_audit(20)


class RetentionService:
    def __init__(self, repo: Repository, settings: Settings, clock: Clock = utcnow) -> None:
        self.repo = repo
        self.settings = settings
        self.clock = clock

    async def purge(self) -> dict[str, int]:
        now = self.clock()
        return await self.repo.purge_old_data(
            listings_before=now - timedelta(days=self.settings.retention_listings_days),
            runs_before=now - timedelta(days=self.settings.retention_runs_days),
            notifications_before=now - timedelta(days=self.settings.retention_notifications_days),
        )
