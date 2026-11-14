# -*- coding: utf-8 -*-
"""账号服务：聚合森空岛扫码 / B 服扫码两条登录链路，异步拉取并广播账号状态。

约束（docs/CONTRACT.md §7）：
  · ``state`` 是只读契约字典；``snapshot()`` 给插件白名单用，返回深拷贝；
  · 所有网络走 ``endbox.core.workers.run_async``，回调回到 UI 线程；
  · 接口失败保留凭据，错误以人话写进 ``state["error"]``，不吞异常；
  · 演示数据 ``demo=True``，界面必须明确标注。
"""
from __future__ import annotations

import copy
import time
from typing import Any, Callable

from PyQt6.QtCore import QObject

from endbox.app_context import AppContext
from endbox.core.event_bus import Topics, bus
from endbox.core.log import get_logger
from endbox.core.text import t
from endbox.core.workers import run_async
from endbox.modules.account import bilibili, skland
from endbox.modules.account.models import (
    PROVIDER_BILIBILI,
    PROVIDER_SKLAND,
    AccountError,
    blank_state,
    demo_state,
    state_from_profile,
)

log = get_logger("account")

# B 服登录后仍然缺少角色数据时的提示（不是失败，但不该假装有数据）
BILI_ROLE_HINT = t("account.service.bili_role_hint")


def _now() -> int:
    return int(time.time())


def _human_error(exc: Exception, prefix: str | None = None) -> str:
    if isinstance(exc, AccountError):
        return exc.display()
    return t(
        "account.service.error_detail",
        prefix=prefix or t("account.service.sync_failed"),
        error=exc,
    )


def _role_cache_key(profile: Any) -> tuple[str, str] | None:
    """当前选中角色在内存缓存里的键（roleId, serverId）；缺一为 None。"""
    role = skland.pick_role(
        getattr(profile, "roles", []) or [], getattr(profile, "role_index", 0)
    )
    if role is None or not role.api_ready:
        return None
    return (str(role.role_id), str(role.server_id))


class AccountService(QObject):
    """账号中心服务。构造时只读本地凭据，不发起网络请求。"""

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.settings = ctx.settings
        self.state: dict[str, Any] = blank_state()
        self._closed = False
        self._refreshing = False
        self._generation = 0
        # card/detail 内存缓存：只缓存成功拉取的完整数据，用于「绑定成功但完整数据
        # 暂时失败」时降级显示上次结果（界面会明确标注可能不是最新）。
        self._card_cache: dict[tuple[str, str], Any] = {}

        provider = str(self.settings.get("account.provider", "") or "")
        if provider in (PROVIDER_SKLAND, PROVIDER_BILIBILI):
            self.state["provider"] = provider
        self.state["last_sync"] = int(self.settings.get("account.last_sync", 0) or 0)
        self.state["uid_masked"] = str(self.settings.get("account.uid_masked", "") or "") or None
        self.state["role_index"] = self._role_index()

    # -- 只读查询 -----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """给插件 / HUD 的只读快照（深拷贝，防止外部改动内部状态）。"""
        return copy.deepcopy(self.state)

    def demo_enabled(self) -> bool:
        return bool(self.settings.get("account.demo_mode", False))

    def has_credentials(self) -> bool:
        return skland.has_credential() or bool(bilibili.load_cookies().get("SESSDATA"))

    def is_busy(self) -> bool:
        return self._refreshing

    def _role_index(self) -> int:
        try:
            return max(0, int(self.settings.get("account.role_index", 0) or 0))
        except (TypeError, ValueError):
            return 0

    # -- 刷新 ---------------------------------------------------------------
    def refresh(
        self,
        *,
        on_done: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """异步刷新；完成后 emit account.state / account.updated。"""
        if self._closed or self._refreshing:
            return
        if self.demo_enabled():
            self._publish(demo_state(), persist=False)
            if on_done:
                on_done(self.snapshot())
            return

        credential = skland.load_credential()
        cookies = bilibili.load_cookies()
        if credential:
            self._refresh_skland(credential, on_done=on_done, on_error=on_error)
        elif cookies.get("SESSDATA"):
            self._refresh_bilibili(cookies, on_done=on_done, on_error=on_error)
        else:
            state = blank_state(
                provider="",
                last_sync=int(self.settings.get("account.last_sync", 0) or 0),
            )
            self._publish(state, persist=False)
            if on_done:
                on_done(self.snapshot())

    def _refresh_skland(
        self,
        credential: dict[str, str],
        *,
        on_done: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._refreshing = True
        generation = self._generation
        base = copy.deepcopy(self.state)
        base.update(provider=PROVIDER_SKLAND, online=False, demo=False, error=None)
        self._publish(base)

        def done(result) -> None:
            if self._closed or generation != self._generation:
                return
            self._refreshing = False
            profile, refreshed = result
            try:
                skland.save_credential(refreshed)
            except Exception:  # noqa: BLE001 - 存储失败不该丢掉本次资料
                log.exception("persist refreshed skland credential failed")
            cache_key = _role_cache_key(profile)
            if profile.card is not None and cache_key is not None:
                self._card_cache[cache_key] = profile.card
            elif profile.card is None and cache_key is not None:
                cached = self._card_cache.get(cache_key)
                if cached is not None:
                    # 绑定列表成功、完整数据暂时失败：回退到上次成功结果，并明确标注可能过期
                    profile.card = cached
                    reason = profile.data_error or t("account.service.sync_failed")
                    profile.data_error = t("account.data.cached", reason=reason)
            state = state_from_profile(profile, last_sync=_now(), demo=False)
            self._publish(state)
            log.info(
                "skland sync ok: %s role(s), %s operator(s), player=%s",
                len(state["roles"]),
                len(state["operators"]),
                bool(state["player"]),
            )
            if on_done:
                on_done(self.snapshot())

        def failed(exc: Exception) -> None:
            if self._closed or generation != self._generation:
                return
            self._refreshing = False
            message = _human_error(exc, t("account.service.skland_sync_failed"))
            state = copy.deepcopy(self.state)
            state.update(online=False, provider=PROVIDER_SKLAND, demo=False, error=message)
            self._publish(state)
            log.warning("skland sync failed: %s", exc)
            if on_error:
                on_error(exc)

        run_async(
            skland.fetch_player_data_with_refresh,
            credential,
            role_index=self._role_index(),
            on_done=done,
            on_error=failed,
        )

    def _refresh_bilibili(
        self,
        cookies: dict[str, str],
        *,
        on_done: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._refreshing = True
        generation = self._generation
        base = copy.deepcopy(self.state)
        base.update(provider=PROVIDER_BILIBILI, online=False, demo=False, error=None)
        self._publish(base)

        def done(profile) -> None:
            if self._closed or generation != self._generation:
                return
            self._refreshing = False
            state = state_from_profile(profile, last_sync=_now(), demo=False)
            state["error"] = BILI_ROLE_HINT
            self._publish(state)
            log.info("bilibili sync ok")
            if on_done:
                on_done(self.snapshot())

        def failed(exc: Exception) -> None:
            if self._closed or generation != self._generation:
                return
            self._refreshing = False
            message = _human_error(exc, t("account.service.bili_sync_failed"))
            state = copy.deepcopy(self.state)
            state.update(online=False, provider=PROVIDER_BILIBILI, demo=False, error=message)
            self._publish(state)
            log.warning("bilibili sync failed: %s", exc)
            if on_error:
                on_error(exc)

        run_async(bilibili.fetch_profile, cookies, on_done=done, on_error=failed)

    # -- 森空岛扫码 ---------------------------------------------------------
    def skland_scan_start(
        self,
        *,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if self._closed:
            return
        generation = self._generation

        def done(qr) -> None:
            if not self._closed and generation == self._generation and on_done:
                on_done(qr)

        def failed(exc: Exception) -> None:
            if not self._closed and generation == self._generation and on_error:
                on_error(exc)

        run_async(skland.create_scan_login, on_done=done, on_error=failed)

    def skland_scan_poll(
        self,
        scan_id: str,
        *,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if self._closed:
            return
        generation = self._generation

        def done(result) -> None:
            if not self._closed and generation == self._generation and on_done:
                on_done(result)

        def failed(exc: Exception) -> None:
            if not self._closed and generation == self._generation and on_error:
                on_error(exc)

        run_async(skland.check_scan_status, scan_id, on_done=done, on_error=failed)

    def skland_scan_confirm(
        self,
        scan_code: str,
        *,
        on_done: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """扫码确认后换取 cred 并保存；成功即登录完成。"""
        if self._closed:
            return
        generation = self._generation
        self._refreshing = True
        self.settings.set("account.provider", PROVIDER_SKLAND, save=False)
        self.settings.set("account.demo_mode", False, save=False)
        self.settings.save()

        def done(result) -> None:
            if self._closed or generation != self._generation:
                return
            self._refreshing = False
            try:
                skland.save_credential(result.credential)
            except Exception as exc:  # noqa: BLE001
                self._refreshing = False
                if on_error:
                    on_error(
                        AccountError(
                            t("account.service.credential_save_failed", error=exc),
                            hint=t("account.service.credential_save_hint"),
                        )
                    )
                return
            state = state_from_profile(result.profile, last_sync=_now(), demo=False)
            self._publish(state)
            log.info("skland scan login ok: %s role(s)", len(state["roles"]))
            if on_done:
                on_done(self.snapshot())

        def failed(exc: Exception) -> None:
            if self._closed or generation != self._generation:
                return
            self._refreshing = False
            message = _human_error(exc, t("account.service.skland_login_failed"))
            state = copy.deepcopy(self.state)
            state.update(online=False, provider=PROVIDER_SKLAND, demo=False, error=message)
            self._publish(state)
            log.warning("skland scan login failed: %s", exc)
            if on_error:
                on_error(exc)

        run_async(skland.complete_scan_login, scan_code, on_done=done, on_error=failed)

    # -- 多角色选择 / 签到（card/detail 相关）--------------------------------
    def select_role(
        self,
        index: int,
        *,
        on_done: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """切换当前展示的终末地角色（持久化序号，重新拉 card/detail）。"""
        if self._closed:
            return
        try:
            index = max(0, int(index))
        except (TypeError, ValueError):
            return
        roles = list(self.state.get("roles") or [])
        if roles:
            index = min(index, len(roles) - 1)
        if index == self._role_index():
            return
        self.settings.set("account.role_index", index)
        self._generation += 1
        self._refreshing = False
        # 立刻把角色序号写进快照，界面可以先把选择器切过去（数据随后到）
        self.state["role_index"] = index
        self.refresh(on_done=on_done, on_error=on_error)

    def skland_sign_attendance(
        self,
        *,
        on_done: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """终末地每日签到（社区记录接口，未端到端验证；失败不阻塞主流程）。"""
        if self._closed or self._refreshing:
            return
        credential = skland.load_credential()
        if not credential:
            if on_error:
                on_error(
                    AccountError(
                        t("account.service.no_credential"),
                        hint=t("account.service.no_credential_hint"),
                    )
                )
            return
        generation = self._generation
        self._refreshing = True

        def done(result) -> None:
            if self._closed or generation != self._generation:
                return
            self._refreshing = False
            data, refreshed = result
            try:
                skland.save_credential(refreshed)
            except Exception:  # noqa: BLE001 - 签到已成功，落盘失败只记日志
                log.exception("persist refreshed skland credential failed")
            awards = data.get("awards") if isinstance(data, dict) else None
            payload = {
                "awards": len(awards) if isinstance(awards, list) else 0,
                "data": data if isinstance(data, dict) else {},
            }
            log.info("endfield attendance ok: %s award(s)", payload["awards"])
            if on_done:
                on_done(payload)

        def failed(exc: Exception) -> None:
            if self._closed or generation != self._generation:
                return
            self._refreshing = False
            log.warning("endfield attendance failed: %s", exc)
            if on_error:
                on_error(exc)

        run_async(
            skland.sign_attendance_for,
            credential,
            role_index=self._role_index(),
            on_done=done,
            on_error=failed,
        )

    # -- B 服扫码 -----------------------------------------------------------
    def bili_generate(
        self,
        *,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if self._closed:
            return
        generation = self._generation

        def done(qr) -> None:
            if not self._closed and generation == self._generation and on_done:
                on_done(qr)

        def failed(exc: Exception) -> None:
            if not self._closed and generation == self._generation and on_error:
                on_error(exc)

        run_async(bilibili.generate_qr, on_done=done, on_error=failed)

    def bili_poll(
        self,
        qrcode_key: str,
        *,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if self._closed:
            return
        generation = self._generation

        def done(result) -> None:
            if not self._closed and generation == self._generation and on_done:
                on_done(result)

        def failed(exc: Exception) -> None:
            if not self._closed and generation == self._generation and on_error:
                on_error(exc)

        run_async(bilibili.poll_qr, qrcode_key, on_done=done, on_error=failed)

    def commit_bili_login(
        self,
        cookies: dict[str, str],
        *,
        on_done: Callable[[dict[str, Any]], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """扫码成功：加密保存 Cookie 并拉一次 B 站资料。"""
        if self._closed:
            return
        bilibili.save_cookies(cookies)
        self.settings.set("account.provider", PROVIDER_BILIBILI, save=False)
        self.settings.set("account.demo_mode", False, save=False)
        self.settings.save()
        self._generation += 1
        self._refreshing = False
        self._refresh_bilibili(
            bilibili.load_cookies(), on_done=on_done, on_error=on_error
        )

    # -- 演示 / 退出 --------------------------------------------------------
    def set_demo_mode(self, enabled: bool) -> None:
        """演示模式：本地假数据，明确标注 demo=True；不触碰真实凭据。"""
        if self._closed:
            return
        enabled = bool(enabled)
        self.settings.set("account.demo_mode", enabled)
        self._generation += 1
        self._refreshing = False
        if enabled:
            self._publish(demo_state(), persist=False)
        else:
            state = blank_state(
                provider=str(self.settings.get("account.provider", "") or ""),
                last_sync=int(self.settings.get("account.last_sync", 0) or 0),
            )
            self._publish(state, persist=False)
            self.refresh()

    def logout(self) -> None:
        """清空所有凭据与演示状态，回到未连接。"""
        self._generation += 1
        self._refreshing = False
        try:
            skland.clear_credential()
        except Exception:  # noqa: BLE001 - 存储损坏也不该让登出失败
            log.exception("clear skland credential failed")
        try:
            bilibili.clear_cookies()
        except Exception:  # noqa: BLE001
            log.exception("clear bilibili cookies failed")
        self.settings.set("account.provider", "", save=False)
        self.settings.set("account.demo_mode", False, save=False)
        self.settings.set("account.uid_masked", "", save=False)
        self.settings.set("account.last_sync", 0, save=False)
        self.settings.set("account.role_index", 0, save=False)
        self.settings.save()
        self._card_cache.clear()
        self._publish(blank_state())
        log.info("account logged out")

    def stop(self) -> None:
        self._closed = True
        self._generation += 1
        self._refreshing = False

    # -- 内部 ---------------------------------------------------------------
    def _publish(self, state: dict[str, Any], *, persist: bool = True) -> None:
        if self._closed:
            return
        self.state = state
        if persist:
            self.settings.set("account.provider", state.get("provider") or "", save=False)
            self.settings.set("account.uid_masked", state.get("uid_masked") or "", save=False)
            self.settings.set("account.last_sync", int(state.get("last_sync") or 0), save=False)
            self.settings.save()
        snapshot = self.snapshot()
        bus.emit(
            Topics.ACCOUNT_STATE,
            nickname=snapshot.get("nickname"),
            online=bool(snapshot.get("online")),
            avatar_path=snapshot.get("avatar_path"),
            provider=snapshot.get("provider") or "",
            roles=snapshot.get("roles") or [],
        )
        bus.emit(Topics.ACCOUNT_UPDATED, snapshot=snapshot)


def create_service(ctx: AppContext) -> AccountService:
    """工厂：endbox/app.py 启动时调用。"""
    return AccountService(ctx)
