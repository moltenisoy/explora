from __future__ import annotations

import json
import logging
import logging.config
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QApplication

import config_manager
import navigation_controller
import theme_manager
import ui_main


APP_NAME = "ModernWindowsFileExplorer"
APP_ORG_NAME = "YourCompany"
APP_ORG_DOMAIN = "local.app"
DEFAULT_LOG_LEVEL = "INFO"
WINDOWS_DEFAULT_CONFIG_PATH = str(Path.home() / "AppData" / "Roaming" / "explora" / "config.json")


class JsonFormatter(logging.Formatter):

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process": record.process,
            "thread": record.thread,
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extra_fields = {
            key: value
            for key, value in record.__dict__.items()
            if key
            not in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "message",
                "asctime",
            }
        }
        if extra_fields:
            payload["extra"] = extra_fields

        return json.dumps(payload, ensure_ascii=False)


@dataclass(slots=True)
class AppContext:

    app: QApplication
    config: Any
    logger: logging.Logger
    navigation: Any | None = None
    main_window: Any | None = None
    plugin_manager: "PluginManager | None" = None
    twin_instance: "TwinInstanceCoordinator | None" = None


class AppSignals(QObject):

    app_started = Signal()
    about_to_shutdown = Signal()
    theme_changed = Signal(str)
    instance_message_received = Signal(dict)


class PluginManager:

    def __init__(self, context: AppContext) -> None:
        self._context = context
        self._plugins: list[Any] = []

    def discover_and_load(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


class GlobalEventRegistry(QObject):

    def __init__(self, context: AppContext, signals_bus: AppSignals) -> None:
        super().__init__()
        self._context = context
        self._signals = signals_bus
        self._actions: list[QAction] = []

    def register(self) -> None:
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self._request_shutdown)
        self._actions.append(quit_action)

        self._context.app.aboutToQuit.connect(self._on_about_to_quit)

    def _request_shutdown(self) -> None:
        self._context.app.quit()

    def _on_about_to_quit(self) -> None:
        self._signals.about_to_shutdown.emit()


class TwinInstanceCoordinator(QObject):

    instance_message = Signal(dict)

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self._context = context
        self._mode = "standalone"
        self._instance_id = os.getpid()

    def initialize(self) -> None:
        twin_cfg = self._safe_get("runtime.twin_instance", default={})
        enabled = bool(twin_cfg.get("enabled", False))
        self._mode = "twin" if enabled else "standalone"

    def broadcast(self, payload: dict[str, Any]) -> None:
        self.instance_message.emit(payload)

    def shutdown(self) -> None:
        pass

    def _safe_get(self, dotted_path: str, default: Any = None) -> Any:
        current = self._context.config
        for part in dotted_path.split("."):
            if isinstance(current, dict):
                current = current.get(part, default)
            else:
                current = getattr(current, part, default)
            if current is default:
                break
        return current


class ApplicationBootstrapper:

    def __init__(self) -> None:
        self.signals = AppSignals()
        self.context: AppContext | None = None
        self._global_events: GlobalEventRegistry | None = None

    def run(self) -> int:
        self._configure_qt_application_metadata()
        app = self._create_application()
        logger = self._configure_logging()
        config = self._load_global_config(logger)

        self.context = AppContext(app=app, config=config, logger=logger)

        self._configure_runtime()
        self._initialize_theming()
        self._initialize_animations()
        self._initialize_navigation()
        self._initialize_plugins()
        self._initialize_instance_support()
        self._register_global_events()
        self._create_and_show_main_window()
        self._register_os_signal_handlers()

        self.signals.app_started.emit()

        return app.exec()

    def _configure_qt_application_metadata(self) -> None:
        QGuiApplication.setApplicationName(APP_NAME)
        QGuiApplication.setOrganizationName(APP_ORG_NAME)
        QGuiApplication.setOrganizationDomain(APP_ORG_DOMAIN)

    def _create_application(self) -> QApplication:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(True)
        return app

    def _configure_logging(self) -> logging.Logger:
        log_dir = Path.cwd() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "app.log"

        logging.config.dictConfig(
            {
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {
                    "json": {
                        "()": JsonFormatter,
                    },
                    "plain": {
                        "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                    },
                },
                "handlers": {
                    "console": {
                        "class": "logging.StreamHandler",
                        "level": DEFAULT_LOG_LEVEL,
                        "formatter": "plain",
                        "stream": "ext://sys.stdout",
                    },
                    "file": {
                        "class": "logging.handlers.RotatingFileHandler",
                        "level": DEFAULT_LOG_LEVEL,
                        "formatter": "json",
                        "filename": str(log_file),
                        "maxBytes": 5 * 1024 * 1024,
                        "backupCount": 5,
                        "encoding": "utf-8",
                    },
                },
                "root": {
                    "level": DEFAULT_LOG_LEVEL,
                    "handlers": ["console", "file"],
                },
            }
        )

        logger = logging.getLogger("app")
        return logger

    def _load_global_config(self, logger: logging.Logger) -> Any:
        try:
            if hasattr(config_manager, "load_global_config"):
                config = config_manager.load_global_config()
            elif hasattr(config_manager, "load"):
                config = config_manager.load()
            elif hasattr(config_manager, "ConfigManager"):
                manager = config_manager.ConfigManager(
                    storage_backend="json",
                    storage_path=WINDOWS_DEFAULT_CONFIG_PATH,
                )
                config = manager
            else:
                raise RuntimeError(
                    "config_manager must expose load_global_config(), load(), or ConfigManager"
                )

            return config

        except Exception:
            logger.exception("No se pudo cargar la configuración global.")
            raise

    def _configure_runtime(self) -> None:
        assert self.context is not None

    def _initialize_theming(self) -> None:
        assert self.context is not None

        theme_name = self._config_get("ui.theme.mode", default="dark")

        if hasattr(theme_manager, "ThemeManager"):
            manager = theme_manager.ThemeManager(self.context.app, self.context.config)
            if hasattr(manager, "set_theme"):
                manager.set_theme(str(theme_name))
            elif hasattr(manager, "apply_current_theme"):
                manager.apply_current_theme()

        self.signals.theme_changed.emit(str(theme_name))

    def _initialize_animations(self) -> None:
        assert self.context is not None

        animations_enabled = bool(self._config_get("ui.animations.enabled", default=True))
        duration_scale = float(self._config_get("ui.animations.duration_scale", default=1.0))

        if hasattr(self.context.app, "setProperty"):
            self.context.app.setProperty("animations_enabled", animations_enabled)
            self.context.app.setProperty("animation_duration_scale", duration_scale)

    def _initialize_navigation(self) -> None:
        assert self.context is not None

        if hasattr(navigation_controller, "NavigationController"):
            self.context.navigation = navigation_controller.NavigationController()
        elif hasattr(navigation_controller, "create"):
            self.context.navigation = navigation_controller.create(self.context.config)
        else:
            self.context.navigation = None

    def _initialize_plugins(self) -> None:
        assert self.context is not None
        self.context.plugin_manager = PluginManager(self.context)
        self.context.plugin_manager.discover_and_load()

    def _initialize_instance_support(self) -> None:
        assert self.context is not None

        coordinator = TwinInstanceCoordinator(self.context)
        coordinator.initialize()
        coordinator.instance_message.connect(self.signals.instance_message_received.emit)

        self.context.twin_instance = coordinator

    def _register_global_events(self) -> None:
        assert self.context is not None
        self._global_events = GlobalEventRegistry(self.context, self.signals)
        self._global_events.register()

    def _create_and_show_main_window(self) -> None:
        assert self.context is not None

        factory_candidates: list[Callable[[], Any] | None] = [
            getattr(ui_main, "create_main_window", None),
            getattr(ui_main, "build_main_window", None),
            getattr(ui_main, "MainWindow", None),
            getattr(ui_main, "MainExplorerUI", None),
        ]

        window: Any | None = None
        for candidate in factory_candidates:
            if candidate is None:
                continue

            if candidate.__name__ in {"MainWindow", "MainExplorerUI"}:
                try:
                    window = candidate(
                        config=self.context.config,
                        navigation=self.context.navigation,
                    )
                except TypeError:
                    try:
                        window = candidate(self.context.config, self.context.navigation)
                    except TypeError:
                        window = candidate()
            else:
                try:
                    window = candidate(
                        config=self.context.config,
                        navigation=self.context.navigation,
                    )
                except TypeError:
                    try:
                        window = candidate(self.context.config, self.context.navigation)
                    except TypeError:
                        window = candidate()
            if window is not None:
                break

        if window is None:
            raise RuntimeError(
                "ui_main must expose create_main_window(), build_main_window(), or MainWindow"
            )

        self.context.main_window = window

        nav = self.context.navigation
        if nav is not None and hasattr(window, "back_requested"):
            try:
                nav.create_view("pane_0")
            except Exception:
                pass
            try:
                nav.create_view("pane_1")
            except Exception:
                pass

            def _pane_id(index: int) -> str:
                return f"pane_{index}"

            window.back_requested.connect(lambda idx: nav.go_back(_pane_id(idx)))
            window.forward_requested.connect(lambda idx: nav.go_forward(_pane_id(idx)))
            window.up_requested.connect(lambda idx: nav.go_up(_pane_id(idx)))
            window.path_submitted.connect(
                lambda idx, p: nav.sync_from_address_bar(_pane_id(idx), p)
            )

            def _on_nav_event(event: dict) -> None:
                view_id = event.get("view_id", "")
                if not view_id.startswith("pane_"):
                    return
                try:
                    pane_index = int(view_id.split("_", 1)[1])
                except (ValueError, IndexError):
                    return
                state = event.get("state", {})
                path = state.get("current_path")
                if path and hasattr(window, "set_path"):
                    window.set_path(pane_index, path)
                if hasattr(window, "set_navigation_enabled"):
                    window.set_navigation_enabled(
                        pane_index,
                        back=state.get("can_go_back", False),
                        forward=state.get("can_go_forward", False),
                        up=state.get("can_go_up", False),
                    )

            nav.add_global_listener(_on_nav_event)

        if hasattr(window, "show"):
            window.show()

    def _register_os_signal_handlers(self) -> None:
        assert self.context is not None

        def _handle_signal(signum: int, _frame: Any) -> None:
            QTimer.singleShot(0, self.context.app.quit)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle_signal)
            except Exception:
                self.context.logger.debug("No se pudo registrar la señal %s", sig)

    def shutdown(self) -> None:
        if self.context is None:
            return

        try:
            if self.context.main_window and hasattr(self.context.main_window, "close"):
                self.context.main_window.close()
        except Exception:
            self.context.logger.exception("Error al cerrar la ventana principal.")

        try:
            if self.context.plugin_manager:
                self.context.plugin_manager.shutdown()
        except Exception:
            self.context.logger.exception("Error al cerrar plugins.")

        try:
            if self.context.twin_instance:
                self.context.twin_instance.shutdown()
        except Exception:
            self.context.logger.exception("Error al cerrar soporte de instancias.")

    def _config_get(self, dotted_path: str, default: Any = None) -> Any:
        assert self.context is not None

        current = self.context.config
        if hasattr(current, "get") and callable(getattr(current, "get")):
            try:
                return current.get(dotted_path, default)
            except Exception:
                return default

        for part in dotted_path.split("."):
            if isinstance(current, dict):
                current = current.get(part, default)
            else:
                current = getattr(current, part, default)
            if current is default:
                break
        return current


def _install_global_exception_hooks(logger: logging.Logger) -> None:
    def _handle_exception(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical(
            "Excepción no controlada",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    sys.excepthook = _handle_exception


def main() -> int:
    bootstrapper = ApplicationBootstrapper()

    try:
        logging.basicConfig(level=logging.INFO)
        exit_code = 0

        app_logger = logging.getLogger("app.bootstrap")
        _install_global_exception_hooks(app_logger)

        exit_code = bootstrapper.run()
        return exit_code

    except Exception:
        logging.getLogger("app.bootstrap").exception("Fallo fatal al iniciar la aplicación.")
        return 1

    finally:
        bootstrapper.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
