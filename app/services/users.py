"""User registration, settings and account deletion."""

from __future__ import annotations

from app.config import Settings
from app.domain.models import User
from app.domain.plans import AccessDenied
from app.domain.timing import Clock, utcnow
from app.services.subscriptions import SubscriptionService
from app.storage.errors import IntegrityError
from app.storage.repo import Repository


class UserService:
    def __init__(self, repo: Repository, subscriptions: SubscriptionService, settings: Settings,
                 clock: Clock = utcnow) -> None:
        self.repo = repo
        self.subs = subscriptions
        self.settings = settings
        self.clock = clock

    async def register(self, telegram_id: int, username: str | None, first_name: str | None) -> tuple[User, bool]:
        """Return (user, created). New users automatically get the free trial (once)."""
        user = await self.repo.get_user_by_telegram_id(telegram_id)
        now = self.clock()
        if user is not None:
            fields = {"last_seen_at": now, "bot_blocked": False}
            if user.deleted_at is not None:  # came back after deleting the account
                fields["deleted_at"] = None
            if username != user.username:
                fields["username"] = username
            await self.repo.update_user(user.id, **fields)
            await self._ensure_personal_access(user)
            return (await self.repo.get_user(user.id)), False
        try:
            user = await self.repo.create_user(telegram_id, username, first_name, self.settings.default_language,
                                               self.settings.default_timezone, now)
        except IntegrityError:  # two /start at once
            return (await self.repo.get_user_by_telegram_id(telegram_id)), False
        if self.settings.personal_mode:
            await self._ensure_personal_access(user)
        else:
            try:
                await self.subs.start_trial(user)
            except AccessDenied:
                pass
        return (await self.repo.get_user(user.id)), True

    async def _ensure_personal_access(self, user: User) -> None:
        """Personal mode: the owner (admin) always has the hidden 'personal' plan; nobody else gets anything."""
        if not self.settings.personal_mode or not self.settings.is_admin(user.telegram_id):
            return
        plan = await self.repo.get_plan_by_code("personal")
        if plan is None:
            return
        current = await self.repo.get_active_subscription(user.id)
        if current is not None and current.plan_id == plan.id and current.is_valid_at(self.clock()):
            return
        await self.subs.activate(user.id, plan, "admin", None)

    async def accept_terms(self, user: User) -> None:
        await self.repo.update_user(user.id, terms_accepted_at=self.clock())

    async def set_quiet_hours(self, user: User, start: int | None, end: int | None) -> None:
        await self.repo.update_user(user.id, quiet_start_hour=start, quiet_end_hour=end)

    async def set_hide_new_sellers(self, user: User, hide: bool) -> None:
        await self.repo.update_user(user.id, hide_new_sellers=hide)

    async def set_price_drop_alerts(self, user: User, enabled: bool) -> None:
        await self.repo.update_user(user.id, notify_price_drops=enabled)

    async def delete_account(self, user: User) -> None:
        """Remove searches, matches and notifications; anonymise the profile.
        Payment records are kept (accounting obligations), see docs/SECURITY.md."""
        async with self.repo.transaction():
            await self.repo.delete_user_data(user.id, self.clock())
