from __future__ import annotations

import json
import logging
import logging.config
import os
import signal
import sys
import traceback
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


class JsonFormatter(logging.Formatter):
    """Formatter de logging estructurado en JSON."""

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
    """Contenedor central de dependencias compartidas."""

    app: QApplication
    config: Any
    logger: logging.Logger
    navigation: Any | None = None
    main_window: Any | None = None
    plugin_manager: "PluginManager | None" = None
    twin_instance: "TwinInstanceCoordinator | None" = None


class AppSignals(QObject):
    """Bus simple de señales globales para extensibilidad futura."""

    app_started = Signal()
    about_to_shutdown = Signal()
    theme_changed = Signal(str)
    instance_message_received = Signal(dict)


class PluginManager:
    """Punto de extensión para sistema de plugins futuro."""

    def __init__(self, context: AppContext) -> None:
        self._context = context
        self._plugins: list[Any] = []

    def discover_and_load(self) -> None:
        self._context.logger.info(
            "Plugin system initialized",
            extra={"component": "plugin_manager", "plugins_loaded": 0},
        )

    def shutdown(self) -> None:
        self._context.logger.info(
            "Plugin system shutdown",
            extra={"component": "plugin_manager"},
        )


class GlobalEventRegistry(QObject):
    """Registro centralizado de eventos globales de la aplicación."""

    def __init__(self, context: AppContext, signals_bus: AppSignals) -> None:
        super().__init__()
        self._context = context
        self._signals = signals_bus
        self._actions: list[QAction] = []

    def register(self) -> None:
        self._context.logger.info(
            "Registering global events",
            extra={"component": "global_event_registry"},
        )

        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self._request_shutdown)
        self._actions.append(quit_action)

        self._context.app.aboutToQuit.connect(self._on_about_to_quit)

    def _request_shutdown(self) -> None:
        self._context.logger.info(
            "Shutdown requested by global action",
            extra={"component": "global_event_registry", "trigger": "Ctrl+Q"},
        )
        self._context.app.quit()

    def _on_about_to_quit(self) -> None:
        self._signals.about_to_shutdown.emit()


class TwinInstanceCoordinator(QObject):
    """
    Preparación para soporte multi-instancia / modo gemelo.

    Esta implementación deja listo el contrato de coordinación para:
    - una política single-instance
    - multi-instance cooperativa
    - modo twin con sincronización de eventos
    """

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

        self._context.logger.info(
            "Twin instance coordinator initialized",
            extra={
                "component": "twin_instance",
                "mode": self._mode,
                "instance_id": self._instance_id,
            },
        )

    def broadcast(self, payload: dict[str, Any]) -> None:
        self._context.logger.debug(
            "Broadcasting instance payload",
            extra={
                "component": "twin_instance",
                "mode": self._mode,
                "payload": payload,
            },
        )
        self.instance_message.emit(payload)

    def shutdown(self) -> None:
        self._context.logger.info(
            "Twin instance coordinator shutdown",
            extra={
                "component": "twin_instance",
                "instance_id": self._instance_id,
            },
        )

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
    """Orquestador de arranque; mantiene main.py libre de lógica de negocio."""

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
        logger.info(
            "Application startup complete",
            extra={"component": "bootstrap", "argv": sys.argv},
        )

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
        logger.info(
            "Structured logging initialized",
            extra={"component": "logging", "log_file": str(log_file)},
        )
        return logger

    def _load_global_config(self, logger: logging.Logger) -> Any:
        try:
            if hasattr(config_manager, "load_global_config"):
                config = config_manager.load_global_config()
            elif hasattr(config_manager, "load"):
                config = config_manager.load()
            elif hasattr(config_manager, "ConfigManager"):
                manager = config_manager.ConfigManager()
                if hasattr(manager, "load"):
                    config = manager.load()
                else:
                    config = manager
            else:
                raise RuntimeError(
                    "config_manager must expose load_global_config(), load(), or ConfigManager"
                )

            logger.info(
                "Global configuration loaded",
                extra={"component": "config"},
            )
            return config

        except Exception:
            logger.exception(
                "Failed to load global configuration",
                extra={"component": "config"},
            )
            raise

    def _configure_runtime(self) -> None:
        assert self.context is not None
        self.context.logger.debug(
            "Runtime configuration applied",
            extra={"component": "runtime"},
        )

    def _initialize_theming(self) -> None:
        assert self.context is not None

        theme_name = self._config_get("ui.theme.mode", default="dark")
        self.context.logger.info(
            "Initializing theme",
            extra={"component": "theme", "theme_mode": theme_name},
        )

        if hasattr(theme_manager, "initialize"):
            theme_manager.initialize(self.context.app, self.context.config)
        elif hasattr(theme_manager, "apply_theme"):
            theme_manager.apply_theme(self.context.app, theme_name, self.context.config)
        elif hasattr(theme_manager, "ThemeManager"):
            manager = theme_manager.ThemeManager(self.context.app, self.context.config)
            if hasattr(manager, "apply"):
                manager.apply(theme_name)
        else:
            self.context.logger.warning(
                "No compatible theme manager entrypoint found",
                extra={"component": "theme", "theme_mode": theme_name},
            )

        self.signals.theme_changed.emit(str(theme_name))

    def _initialize_animations(self) -> None:
        assert self.context is not None

        animations_enabled = bool(self._config_get("ui.animations.enabled", default=True))
        duration_scale = float(self._config_get("ui.animations.duration_scale", default=1.0))

        self.context.logger.info(
            "Initializing animations",
            extra={
                "component": "animations",
                "enabled": animations_enabled,
                "duration_scale": duration_scale,
            },
        )

        if hasattr(self.context.app, "setProperty"):
            self.context.app.setProperty("animations_enabled", animations_enabled)
            self.context.app.setProperty("animation_duration_scale", duration_scale)

    def _initialize_navigation(self) -> None:
        assert self.context is not None

        if hasattr(navigation_controller, "NavigationController"):
            self.context.navigation = navigation_controller.NavigationController(
                config=self.context.config
            )
        elif hasattr(navigation_controller, "create"):
            self.context.navigation = navigation_controller.create(self.context.config)
        else:
            self.context.navigation = None

        self.context.logger.info(
            "Navigation controller initialized",
            extra={
                "component": "navigation",
                "available": self.context.navigation is not None,
            },
        )

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
        ]

        window: Any | None = None
        for candidate in factory_candidates:
            if candidate is None:
                continue

            if candidate.__name__ in {"MainWindow"}:
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

        if hasattr(window, "show"):
            window.show()

        self.context.logger.info(
            "Main window launched",
            extra={"component": "ui", "window_class": type(window).__name__},
        )

    def _register_os_signal_handlers(self) -> None:
        assert self.context is not None

        def _handle_signal(signum: int, _frame: Any) -> None:
            self.context.logger.info(
                "OS shutdown signal received",
                extra={"component": "signals", "signal": signum},
            )
            QTimer.singleShot(0, self.context.app.quit)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle_signal)
            except (ValueError, OSError) as exc:
                self.context.logger.warning(
                    "Could not register OS signal handler",
                    extra={"component": "signals", "signal": int(sig), "error": str(exc)},
                )

    def shutdown(self) -> None:
        if self.context is None:
            return

        logger = self.context.logger
        logger.info("Starting safe shutdown", extra={"component": "shutdown"})

        try:
            if self.context.main_window and hasattr(self.context.main_window, "close"):
                self.context.main_window.close()
        except Exception:
            logger.exception(
                "Error while closing main window",
                extra={"component": "shutdown"},
            )

        try:
            if self.context.plugin_manager:
                self.context.plugin_manager.shutdown()
        except Exception:
            logger.exception(
                "Error while shutting down plugins",
                extra={"component": "shutdown"},
            )

        try:
            if self.context.twin_instance:
                self.context.twin_instance.shutdown()
        except Exception:
            logger.exception(
                "Error while shutting down twin instance coordinator",
                extra={"component": "shutdown"},
            )

        logger.info("Safe shutdown completed", extra={"component": "shutdown"})

    def _config_get(self, dotted_path: str, default: Any = None) -> Any:
        assert self.context is not None

        current = self.context.config
        for part in dotted_path.split("."):
            if isinstance(current, dict):
                current = current.get(part, default)
            else:
                current = getattr(current, part, default)
            if current is default:
                break
        return current


def _install_global_exception_hooks(logger: logging.Logger) -> None:
    def handle_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: Any,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger.critical(
            "Unhandled exception",
            extra={
                "component": "exceptions",
                "traceback": "".join(
                    traceback.format_exception(exc_type, exc_value, exc_traceback)
                ),
            },
        )

    sys.excepthook = handle_exception


def main() -> int:
    bootstrapper = ApplicationBootstrapper()
    pre_logger = logging.getLogger("bootstrap")

    try:
        logging.basicConfig(level=logging.INFO)
        exit_code = 0

        app_logger = logging.getLogger("app.bootstrap")
        _install_global_exception_hooks(app_logger)

        exit_code = bootstrapper.run()
        return exit_code

    except Exception as exc:
        pre_logger.exception("Fatal error during application bootstrap: %s", exc)
        return 1

    finally:
        bootstrapper.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())