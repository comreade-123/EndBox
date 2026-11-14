# -*- coding: utf-8 -*-
"""插件宿主：扫描、加载、隔离、生命周期与启用/禁用。

约定（docs/CONTRACT.md §10）：

  · 扫描根目录 ``plugins/``（随程序分发）与 ``%APPDATA%/EndBox/plugins/``（用户安装）；
  · 每个插件一个目录，``plugin.json`` + 入口 ``.py``；
  · 入口里必须定义 ``Plugin`` 类（建议继承 ``endbox.plugins.api.EndBoxPlugin``）；
  · 导入失败、manifest 损坏、``on_load`` 崩溃、循环引用全部在这里被捕获并记录成
    「加载失败」条目，主程序照常运行；
  · ``load_all()`` 幂等：重复调用不会重复注册视图。
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from endbox import config
from endbox.core.event_bus import Topics
from endbox.core.log import get_logger
from endbox.core.text import t
from endbox.plugins.api import EndBoxPlugin, PluginContext
from endbox.plugins.manifest import ManifestError, PluginManifest
from endbox.plugins.storage import PluginStorage, storage_filename
from endbox.ui.views.base import ViewRegistry

log = get_logger("plugins.host")

STATE_SCANNED = "scanned"
STATE_LOADING = "loading"
STATE_LOADED = "loaded"
STATE_DISABLED = "disabled"
STATE_ERROR = "error"
STATE_UNLOADED = "unloaded"

STATE_LABELS = {
    STATE_SCANNED: t("plugins.state.scanned"),
    STATE_LOADING: t("plugins.state.loading"),
    STATE_LOADED: t("plugins.state.loaded"),
    STATE_DISABLED: t("plugins.state.disabled"),
    STATE_ERROR: t("plugins.state.error"),
    STATE_UNLOADED: t("plugins.state.unloaded"),
}


class PluginLoadError(RuntimeError):
    """插件加载期的错误（消息会显示在插件管理页）。"""


@dataclass
class PluginRecord:
    """一个插件的全部状态（管理页只读这份数据）。"""

    path: Path
    source: str = "bundled"          # bundled（随程序分发）| user（用户安装）
    manifest: PluginManifest | None = None
    state: str = STATE_SCANNED
    error: str | None = None
    detail: str | None = None
    load_ms: float = 0.0
    instance: Any = None
    module: Any = None
    module_name: str = ""
    context: PluginContext | None = None
    view_keys: list[str] = field(default_factory=list)
    registered_at: float = 0.0

    # -- 展示字段 -----------------------------------------------------------
    @property
    def plugin_id(self) -> str:
        return self.manifest.id if self.manifest is not None else self.path.name

    @property
    def name(self) -> str:
        return self.manifest.name if self.manifest is not None else self.path.name

    @property
    def version(self) -> str:
        return self.manifest.version if self.manifest is not None else "—"

    @property
    def author(self) -> str:
        if self.manifest is None:
            return "—"
        return self.manifest.author or "—"

    @property
    def permissions(self) -> list[str]:
        return list(self.manifest.permissions) if self.manifest is not None else []

    @property
    def ignored_permissions(self) -> list[str]:
        return list(self.manifest.ignored_permissions) if self.manifest is not None else []

    @property
    def state_label(self) -> str:
        return STATE_LABELS.get(self.state, self.state)

    @property
    def is_loaded(self) -> bool:
        return self.state == STATE_LOADED

    @property
    def has_error(self) -> bool:
        return self.state == STATE_ERROR

    def to_dict(self) -> dict[str, Any]:
        """给测试 / 调试用的纯数据视图。"""
        return {
            "id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "path": str(self.path),
            "source": self.source,
            "state": self.state,
            "state_label": self.state_label,
            "error": self.error,
            "permissions": self.permissions,
            "views": list(self.view_keys),
            "load_ms": self.load_ms,
        }


class PluginHost:
    """插件宿主。由 app.py 构造：``PluginHost(ctx, registry)``。"""

    def __init__(
        self,
        ctx: Any,
        registry: ViewRegistry,
        *,
        roots: list[Any] | None = None,
        data_dir: Path | None = None,
    ):
        self.ctx = ctx
        self.registry = registry
        self._roots = self._normalize_roots(roots)
        self._data_dir = Path(data_dir) if data_dir is not None else (config.DATA_DIR / "plugin_data")

        self.records: list[PluginRecord] = []
        self.last_scan_ms: float = 0.0
        self.last_scan_at: float = 0.0

        self._lock = threading.RLock()
        self._loading = False
        self._loaded_once = False

    # -- 公共查询 -----------------------------------------------------------
    @property
    def loaded(self) -> list[PluginRecord]:
        """已加载（启用）的插件记录；设置页读它的长度。"""
        return [record for record in self.records if record.state == STATE_LOADED]

    @property
    def plugins(self) -> list[PluginRecord]:
        """全部发现到的插件记录（含失败、禁用）。"""
        return list(self.records)

    def get(self, plugin_id: str) -> PluginRecord | None:
        for record in self.records:
            if record.plugin_id == plugin_id:
                return record
        return None

    def summary(self) -> dict[str, int]:
        records = self.records
        return {
            "total": len(records),
            "loaded": sum(1 for r in records if r.state == STATE_LOADED),
            "disabled": sum(1 for r in records if r.state == STATE_DISABLED),
            "error": sum(1 for r in records if r.state == STATE_ERROR),
        }

    # -- 扫描 ---------------------------------------------------------------
    def scan(self) -> list[PluginRecord]:
        """只发现插件目录、解析 manifest，不导入任何代码。"""
        with self._lock:
            started = time.perf_counter()
            records: list[PluginRecord] = []
            seen: dict[str, Path] = {}
            for root, source in self._roots:
                if not root.is_dir():
                    log.info("plugin root missing: %s (%s)", root, source)
                    continue
                for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                    if not child.is_dir():
                        continue
                    if child.name.startswith((".", "_")) or child.name == "__pycache__":
                        continue
                    if not (child / "plugin.json").is_file():
                        log.debug("skip non-plugin directory: %s", child)
                        continue
                    record = self._scan_one(child, source, seen)
                    if record is not None:
                        records.append(record)
            records.sort(key=self._sort_key)
            self.records = records
            self.last_scan_ms = (time.perf_counter() - started) * 1000.0
            self.last_scan_at = time.time()
            log.info(
                "plugin scan: %d found (%.1f ms) in %s",
                len(records),
                self.last_scan_ms,
                ", ".join(str(root) for root, _ in self._roots),
            )
            return list(self.records)

    def _scan_one(
        self, child: Path, source: str, seen: dict[str, Path]
    ) -> PluginRecord | None:
        try:
            manifest = PluginManifest.load(child)
        except ManifestError as exc:
            log.warning("plugin manifest invalid: %s: %s", child, exc)
            return PluginRecord(path=child, source=source, state=STATE_ERROR, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - 任何解析异常都只让这一个插件失败
            log.exception("plugin manifest crashed: %s", child)
            return PluginRecord(
                path=child,
                source=source,
                state=STATE_ERROR,
                error=t("plugins.host.error.manifest_crash", error=f"{type(exc).__name__}: {exc}"),
            )

        if manifest.id in seen:
            message = t("plugins.host.error.duplicate", id=manifest.id, path=seen[manifest.id])
            log.warning("duplicate plugin id: %s -> %s", child, message)
            return PluginRecord(
                path=child,
                source=source,
                manifest=manifest,
                state=STATE_ERROR,
                error=message,
            )
        seen[manifest.id] = child
        return PluginRecord(path=child, source=source, manifest=manifest)

    @staticmethod
    def _sort_key(record: PluginRecord) -> tuple:
        order = record.manifest.order if record.manifest is not None else 9999
        name = record.name.lower()
        return (0 if record.manifest is not None else 1, order, name, str(record.path).lower())

    # -- 加载 ---------------------------------------------------------------
    def load_all(self) -> list[PluginRecord]:
        """扫描并加载全部启用的插件；幂等，可被重复调用。"""
        with self._lock:
            if self._loading:
                log.warning("plugin load_all() re-entered, ignored")
                return list(self.records)
            if self._loaded_once:
                log.info("plugins already loaded, load_all() ignored")
                return list(self.records)
            self._loading = True
            try:
                started = time.perf_counter()
                self.scan()
                for record in list(self.records):
                    if record.state == STATE_ERROR:
                        continue
                    if not self._enabled_by_settings(record.plugin_id):
                        record.state = STATE_DISABLED
                        log.info("plugin disabled by settings: %s", record.plugin_id)
                        continue
                    self._start_plugin(record)
                self._loaded_once = True
                summary = self.summary()
                log.info(
                    "plugins loaded: %d loaded, %d disabled, %d failed (%.1f ms)",
                    summary["loaded"],
                    summary["disabled"],
                    summary["error"],
                    (time.perf_counter() - started) * 1000.0,
                )
                self._emit_changed()
                return list(self.records)
            finally:
                self._loading = False

    def rescan(self) -> list[PluginRecord]:
        """卸载全部插件后重新扫描 + 加载（管理页「刷新扫描」）。"""
        with self._lock:
            self._shutdown_records()
            self.records = []
            self._loaded_once = False
            return self.load_all()

    def reload(self, plugin_id: str) -> bool:
        """重新加载单个插件（重新解析 manifest、重建模块、重注册视图）。"""
        with self._lock:
            record = self.get(plugin_id)
            if record is None:
                return False
            if record.state == STATE_LOADED:
                self._teardown(record)
                record.state = STATE_UNLOADED
            self._refresh_manifest(record)
            if record.manifest is None:
                self._emit_changed()
                return False
            if not self._enabled_by_settings(record.plugin_id):
                record.state = STATE_DISABLED
                self._emit_changed()
                return True
            self._start_plugin(record)
            self._emit_changed()
            return record.state == STATE_LOADED

    # -- 启用 / 禁用 --------------------------------------------------------
    def enable(self, plugin_id: str) -> bool:
        with self._lock:
            record = self.get(plugin_id)
            if record is None:
                return False
            self._set_disabled_setting(plugin_id, False)
            if record.state == STATE_LOADED:
                self._emit_changed()
                return True
            self._refresh_manifest(record)
            if record.manifest is None:
                self._emit_changed()
                return False
            self._start_plugin(record)
            log.info("plugin enable result: %s -> %s", plugin_id, record.state)
            self._emit_changed()
            return record.state == STATE_LOADED

    def disable(self, plugin_id: str) -> bool:
        with self._lock:
            record = self.get(plugin_id)
            if record is None:
                return False
            if record.state in (STATE_LOADED, STATE_LOADING):
                self._teardown(record)
            record.state = STATE_DISABLED
            record.error = None
            record.detail = None
            self._set_disabled_setting(plugin_id, True)
            log.info("plugin disabled: %s", plugin_id)
            self._emit_changed()
            return True

    def unload_all(self) -> None:
        """卸载全部插件（程序退出 / 重新扫描前）。"""
        with self._lock:
            self._shutdown_records()
            self._emit_changed()

    def remove(self, plugin_id: str) -> bool:
        """把一条插件记录摘掉（卸载插件 / 删除目录后调用）。

        只负责：调用生命周期、取消视图注册、从记录表移除；**不删除磁盘文件**
        （目录由调用方处理，避免宿主误删随程序分发的插件）。
        """
        with self._lock:
            record = self.get(plugin_id)
            if record is None:
                return False
            if record.state == STATE_LOADED or record.instance is not None:
                self._teardown(record)
            elif record.context is not None:
                self._close_context(record)
            for key in list(record.view_keys):
                self._unregister_view(key)
            record.view_keys.clear()
            self.records = [item for item in self.records if item is not record]
            try:
                self.ctx.settings.set(
                    "plugins.disabled",
                    [
                        item
                        for item in (self.ctx.settings.get("plugins.disabled", []) or [])
                        if item != plugin_id
                    ],
                )
                enabled = [
                    item
                    for item in (self.ctx.settings.get("plugins.enabled", []) or [])
                    if item != plugin_id
                ]
                if enabled:
                    self.ctx.settings.set("plugins.enabled", enabled)
            except Exception:  # noqa: BLE001
                log.exception("remove plugin settings cleanup failed: %s", plugin_id)
            log.info("plugin record removed: %s", plugin_id)
            self._emit_changed()
            return True

    def hot_reload(self, plugin_id: str) -> bool:
        """插件文件被更新后热重载：有记录走 reload，没有就重新扫描。"""
        with self._lock:
            if self.get(plugin_id) is not None:
                return self.reload(plugin_id)
        self.rescan()
        return self.get(plugin_id) is not None and self.get(plugin_id).state == STATE_LOADED

    # -- 视图注册（供 PluginContext 调用） ----------------------------------
    def register_plugin_view(self, record: PluginRecord, view_cls: type) -> str:
        """注册插件视图：宿主统一加前缀、section="plugin"，并记录以便卸载。"""
        base_key = str(getattr(view_cls, "key", "") or "view")
        key = base_key
        suffix = 2
        while self.registry.has(key):
            key = f"{base_key}_{suffix}"
            suffix += 1
        try:
            view_cls.key = key
            view_cls.section = "plugin"
            self.registry.register(view_cls)
        except Exception as exc:  # noqa: BLE001
            raise PluginLoadError(
                t("plugins.host.error.register_view", error=f"{type(exc).__name__}: {exc}")
            ) from exc
        if key not in record.view_keys:
            record.view_keys.append(key)
        log.info("plugin %s registered view: %s", record.plugin_id, key)
        return key

    # -- 内部：加载一个插件 -------------------------------------------------
    def _start_plugin(self, record: PluginRecord) -> None:
        if record.manifest is None:
            record.state = STATE_ERROR
            record.error = record.error or t("plugins.host.error.manifest_invalid")
            return
        started = time.perf_counter()
        record.state = STATE_LOADING
        record.error = None
        record.detail = None
        record.view_keys = []
        try:
            storage = PluginStorage(
                record.manifest.id,
                path=self._data_dir / storage_filename(record.manifest.id),
            )
            plugin_ctx = PluginContext(
                host=self,
                app_ctx=self.ctx,
                record=record,
                manifest=record.manifest,
                plugin_dir=record.path,
                storage=storage,
            )
            record.context = plugin_ctx
            module = self._import_entry(record)
            record.module = module
            plugin_cls = getattr(module, "Plugin", None)
            if not isinstance(plugin_cls, type):
                raise PluginLoadError(
                    t("plugins.host.error.no_plugin_class", entry=record.manifest.entry)
                )
            instance = self._instantiate(plugin_cls, plugin_ctx)
            record.instance = instance
            self._hook(record, "on_load")
            if self._enabled_by_settings(record.plugin_id):
                self._hook(record, "on_enable")
            record.state = STATE_LOADED
            record.registered_at = time.time()
            record.load_ms = (time.perf_counter() - started) * 1000.0
            log.info(
                "plugin loaded: %s v%s (%s) views=%d %.1f ms",
                record.plugin_id,
                record.manifest.version,
                record.source,
                len(record.view_keys),
                record.load_ms,
            )
        except BaseException as exc:  # noqa: BLE001 - 插件代码任何异常（含 SystemExit）都隔离
            record.load_ms = (time.perf_counter() - started) * 1000.0
            record.state = STATE_ERROR
            record.error = self._short_error(exc)
            record.detail = traceback.format_exc()
            log.exception("plugin load failed: %s (%s)", record.plugin_id, record.path)
            self._teardown(record, from_error=True)
            record.state = STATE_ERROR

    def _refresh_manifest(self, record: PluginRecord) -> None:
        """重新读取 plugin.json；失败时把错误写回记录。"""
        try:
            record.manifest = PluginManifest.load(record.path)
            record.error = None
            record.detail = None
        except ManifestError as exc:
            record.manifest = None
            record.state = STATE_ERROR
            record.error = str(exc)
            record.detail = None
            log.warning("plugin manifest invalid: %s: %s", record.path, exc)
        except Exception as exc:  # noqa: BLE001
            record.manifest = None
            record.state = STATE_ERROR
            record.error = t("plugins.host.error.manifest_crash", error=f"{type(exc).__name__}: {exc}")
            log.exception("plugin manifest crashed: %s", record.path)

    def _import_entry(self, record: PluginRecord) -> Any:
        assert record.manifest is not None
        entry_path = record.path / record.manifest.entry
        module_name = f"endbox_plugin_{record.manifest.id.replace('.', '_').replace('-', '_')}"
        self._drop_module(module_name)
        try:
            # 把入口当作包加载：插件目录进入 __path__，入口里可以用
            # ``from .helper import ...`` 按相对导入拆分子文件。
            spec = importlib.util.spec_from_file_location(
                module_name,
                entry_path,
                submodule_search_locations=[str(record.path)],
            )
            if spec is None or spec.loader is None:
                raise PluginLoadError(
                    t("plugins.host.error.entry_module", entry=record.manifest.entry)
                )
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            record.module_name = module_name
            try:
                spec.loader.exec_module(module)
            except BaseException:
                self._drop_module(module_name)
                raise
            return module
        except BaseException:
            raise

    @staticmethod
    def _instantiate(plugin_cls: type, plugin_ctx: PluginContext) -> Any:
        wants_ctx = True
        try:
            signature = inspect.signature(plugin_cls)
            parameters = [
                p
                for p in signature.parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            wants_ctx = bool(parameters) or any(
                p.kind == p.VAR_POSITIONAL for p in signature.parameters.values()
            )
        except (TypeError, ValueError):
            wants_ctx = True
        if issubclass(plugin_cls, EndBoxPlugin):
            wants_ctx = True
        instance = plugin_cls(plugin_ctx) if wants_ctx else plugin_cls()
        if getattr(instance, "ctx", None) is None:
            try:
                instance.ctx = plugin_ctx
            except Exception:  # noqa: BLE001 - 只读对象也不强求
                pass
        return instance

    def _hook(self, record: PluginRecord, name: str) -> None:
        instance = record.instance
        if instance is None:
            return
        hook = getattr(instance, name, None)
        if not callable(hook):
            return
        try:
            hook()
        except BaseException as exc:  # noqa: BLE001 - 生命周期钩子必须隔离
            raise PluginLoadError(
                t("plugins.host.error.hook", hook=name, error=f"{type(exc).__name__}: {exc}")
            ) from exc

    def _call_safe(self, record: PluginRecord, name: str) -> None:
        instance = record.instance
        if instance is None:
            return
        hook = getattr(instance, name, None)
        if not callable(hook):
            return
        try:
            hook()
        except BaseException:  # noqa: BLE001 - 清理阶段的异常只能记录
            log.exception("plugin %s: %s() failed", record.plugin_id, name)

    # -- 内部：卸载 ---------------------------------------------------------
    def _shutdown_records(self) -> None:
        for record in self.records:
            if record.state == STATE_LOADED or record.instance is not None:
                self._teardown(record)
                record.state = STATE_UNLOADED
            else:
                record.view_keys.clear()
                if record.context is not None:
                    self._close_context(record)

    def _teardown(self, record: PluginRecord, *, from_error: bool = False) -> None:
        was_loaded = record.state == STATE_LOADED
        if record.instance is not None:
            if was_loaded and not from_error:
                self._call_safe(record, "on_disable")
            self._call_safe(record, "on_unload")
        self._close_context(record)

        for key in list(record.view_keys):
            self._unregister_view(key)
        record.view_keys.clear()

        if record.module_name:
            self._drop_module(record.module_name)
        record.instance = None
        record.module = None

    def _close_context(self, record: PluginRecord) -> None:
        context = record.context
        if context is not None:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                log.exception("plugin context close failed: %s", record.plugin_id)
            record.context = None

    def _unregister_view(self, key: str) -> None:
        try:
            self._remove_view_from_window(key)
            self.registry.unregister(key)
        except Exception:  # noqa: BLE001
            log.exception("unregister plugin view failed: %s", key)

    def _remove_view_from_window(self, key: str) -> None:
        """把已经在主窗口 QStackedWidget 里的插件视图摘掉，避免悬空指针。"""
        window = getattr(self.ctx, "main_window", None)
        stack = getattr(window, "stack", None)
        if stack is None:
            return
        try:
            for index in range(stack.count() - 1, -1, -1):
                widget = stack.widget(index)
                if getattr(widget, "key", None) == key:
                    stack.removeWidget(widget)
        except Exception:  # noqa: BLE001
            log.exception("remove plugin view from window failed: %s", key)

    @staticmethod
    def _drop_module(module_name: str) -> None:
        for name in [n for n in sys.modules if n == module_name or n.startswith(module_name + ".")]:
            sys.modules.pop(name, None)

    # -- 内部：设置与工具 ---------------------------------------------------
    def _enabled_by_settings(self, plugin_id: str) -> bool:
        try:
            disabled = self.ctx.settings.get("plugins.disabled", []) or []
            enabled = self.ctx.settings.get("plugins.enabled", []) or []
        except Exception:  # noqa: BLE001
            return True
        if not isinstance(disabled, list):
            disabled = []
        if not isinstance(enabled, list):
            enabled = []
        if plugin_id in disabled:
            return False
        if enabled and plugin_id not in enabled:
            return False
        return True

    def _set_disabled_setting(self, plugin_id: str, disabled: bool) -> None:
        try:
            items = list(self.ctx.settings.get("plugins.disabled", []) or [])
        except Exception:  # noqa: BLE001
            items = []
        items = [item for item in items if item != plugin_id]
        if disabled:
            items.append(plugin_id)
        try:
            self.ctx.settings.set("plugins.disabled", items)
        except Exception:  # noqa: BLE001
            log.exception("save plugin settings failed: %s", plugin_id)

    def _emit_changed(self) -> None:
        try:
            self.ctx.bus.emit(Topics.PLUGINS_CHANGED)
        except Exception:  # noqa: BLE001
            log.exception("emit plugins.changed failed")

    @staticmethod
    def _short_error(exc: BaseException) -> str:
        message = str(exc).strip()
        if not message:
            message = type(exc).__name__
        return message

    @staticmethod
    def _normalize_roots(roots: list[Any] | None) -> list[tuple[Path, str]]:
        if roots is None:
            return [
                (config.PLUGINS_DIR, "bundled"),
                (config.USER_PLUGINS_DIR, "user"),
            ]
        normalized: list[tuple[Path, str]] = []
        for item in roots:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                path, source = item
                normalized.append((Path(path), str(source)))
            else:
                path = Path(item)
                source = "user" if path == config.USER_PLUGINS_DIR else "bundled"
                normalized.append((path, source))
        return normalized
