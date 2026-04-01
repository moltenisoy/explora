"""
theme_manager.py

Gestor de temas dinámicos para aplicaciones Qt/PySide/PyQt.

Responsabilidades:
- Aplicar estilos dinámicos a toda la app
- Cambiar en runtime colores, fuentes y backgrounds
- Soportar temas predefinidos y custom
- Integrarse con ConfigManager
- Gestionar animaciones y transiciones suaves
- Generar QSS dinámico sin hardcodear hojas de estilo fijas
- Preparado para extensiones/plugins visuales

Requisitos:
- Qt for Python (PySide6/PyQt6/PySide2/PyQt5)
- config_manager.py disponible e importable

Notas:
- Este módulo evita "hardcodear estilos" como un bloque QSS estático monolítico.
  En su lugar, construye reglas dinámicamente a partir de tokens/roles.
- La aplicación puede registrar mapeos de selectores por componente y plugins
  visuales que aporten nuevas reglas o transformaciones.

Uso rápido:
    app = QApplication(sys.argv)
    config = ConfigManager("json", "config.json")
    theme = ThemeManager(app, config)
    theme.apply_current_theme()

    # Cambiar tema en runtime
    theme.set_theme("dark")
    theme.set_runtime_color("accent", "#FF8800")
    theme.set_runtime_font(size=12)
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from config_manager import ConfigManager


# ============================================================
# Compatibilidad Qt
# ============================================================

QT_API = None

try:  # PySide6
    from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette, QPixmap
    from PySide6.QtWidgets import QApplication, QWidget

    QT_API = "PySide6"
except Exception:
    try:  # PyQt6
        from PyQt6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt
        from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPalette, QPixmap
        from PyQt6.QtWidgets import QApplication, QWidget

        QT_API = "PyQt6"
    except Exception:
        try:  # PySide2
            from PySide2.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt
            from PySide2.QtGui import QColor, QFont, QPalette, QPixmap
            from PySide2.QtWidgets import QApplication, QWidget

            QGuiApplication = QApplication
            QT_API = "PySide2"
        except Exception:
            try:  # PyQt5
                from PyQt5.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt
                from PyQt5.QtGui import QColor, QFont, QPalette, QPixmap
                from PyQt5.QtWidgets import QApplication, QWidget

                QGuiApplication = QApplication
                QT_API = "PyQt5"
            except Exception as exc:
                raise ImportError(
                    "No se pudo importar Qt. Instala PySide6, PyQt6, PySide2 o PyQt5."
                ) from exc


# ============================================================
# Tipos
# ============================================================

StyleDict = Dict[str, Any]
ThemeDict = Dict[str, Any]
SelectorMap = Dict[str, Sequence[str]]


# ============================================================
# Plugins / Extensibilidad
# ============================================================

class ThemePlugin(Protocol):
    """
    Contrato mínimo para plugins visuales futuros.
    """

    def extend_theme(self, theme: ThemeDict) -> ThemeDict:
        ...

    def contribute_selectors(self) -> SelectorMap:
        ...

    def contribute_qss_rules(self, theme: ThemeDict) -> List[str]:
        ...


# ============================================================
# Utilidades internas
# ============================================================

def _deep_copy(data: Any) -> Any:
    return copy.deepcopy(data)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = _deep_copy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = _deep_copy(value)
    return result


def _ensure_hex_color(value: str, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    if len(value) in (7, 9) and value.startswith("#"):
        hex_part = value[1:]
        if all(ch in "0123456789abcdefABCDEF" for ch in hex_part):
            return value.upper()
    return fallback


def _normalize_path(path_value: str) -> str:
    if not isinstance(path_value, str) or not path_value.strip():
        return ""
    return str(Path(path_value).expanduser())


def _qss_quote(value: str) -> str:
    escaped = value.replace("\\", "/").replace('"', '\\"')
    return f'"{escaped}"'


def _font_to_qss(font_family: str, font_size: int, weight: str) -> Dict[str, str]:
    qss_weight_map = {
        "light": "300",
        "normal": "400",
        "medium": "500",
        "bold": "700",
    }
    return {
        "font-family": font_family,
        "font-size": f"{font_size}pt",
        "font-weight": qss_weight_map.get(weight, "400"),
    }


def _to_kebab_case(name: str) -> str:
    return name.strip().replace("_", "-")


# ============================================================
# Temas predefinidos
# ============================================================

BUILTIN_THEMES: Dict[str, ThemeDict] = {
    "dark": {
        "meta": {"name": "dark", "label": "Dark"},
        "ui": {
            "colors": {
                "window_bg": "#1E1E1E",
                "window_fg": "#F5F5F5",
                "panel_bg": "#252526",
                "panel_fg": "#D4D4D4",
                "button_bg": "#0E639C",
                "button_fg": "#FFFFFF",
                "button_hover_bg": "#1177BB",
                "input_bg": "#3C3C3C",
                "input_fg": "#F5F5F5",
                "input_border": "#6A6A6A",
                "accent": "#4FC1FF",
                "success": "#16A34A",
                "warning": "#D97706",
                "error": "#DC2626",
                "selection_bg": "#264F78",
                "selection_fg": "#FFFFFF",
                "tooltip_bg": "#2D2D30",
                "tooltip_fg": "#F5F5F5",
                "menu_bg": "#2D2D30",
                "menu_fg": "#F5F5F5",
                "sidebar_bg": "#333333",
                "sidebar_fg": "#E5E5E5",
                "statusbar_bg": "#007ACC",
                "statusbar_fg": "#FFFFFF",
            },
            "fonts": {
                "family": "Segoe UI",
                "size": 10,
                "weight": "normal",
                "ui_family": "Segoe UI",
                "ui_size": 10,
                "title_family": "Segoe UI Semibold",
                "title_size": 12,
                "mono_family": "Consolas",
                "mono_size": 10,
            },
            "backgrounds": {
                "enabled": False,
                "image_path": "",
                "opacity": 1.0,
                "mode": "cover",
            },
        },
        "animations": {
            "enabled": True,
            "duration_ms": 180,
            "easing": "out_cubic",
        },
    },
    "light": {
        "meta": {"name": "light", "label": "Light"},
        "ui": {
            "colors": {
                "window_bg": "#F7F7F7",
                "window_fg": "#1F1F1F",
                "panel_bg": "#FFFFFF",
                "panel_fg": "#2B2B2B",
                "button_bg": "#E6E6E6",
                "button_fg": "#1A1A1A",
                "button_hover_bg": "#DADADA",
                "input_bg": "#FFFFFF",
                "input_fg": "#1F1F1F",
                "input_border": "#B8B8B8",
                "accent": "#0066CC",
                "success": "#15803D",
                "warning": "#B45309",
                "error": "#B91C1C",
                "selection_bg": "#CFE8FF",
                "selection_fg": "#111111",
                "tooltip_bg": "#F0F0F0",
                "tooltip_fg": "#1F1F1F",
                "menu_bg": "#FFFFFF",
                "menu_fg": "#1F1F1F",
                "sidebar_bg": "#EFEFEF",
                "sidebar_fg": "#1F1F1F",
                "statusbar_bg": "#E0E0E0",
                "statusbar_fg": "#1F1F1F",
            },
            "fonts": {
                "family": "Segoe UI",
                "size": 10,
                "weight": "normal",
                "ui_family": "Segoe UI",
                "ui_size": 10,
                "title_family": "Segoe UI Semibold",
                "title_size": 12,
                "mono_family": "Consolas",
                "mono_size": 10,
            },
            "backgrounds": {
                "enabled": False,
                "image_path": "",
                "opacity": 1.0,
                "mode": "cover",
            },
        },
        "animations": {
            "enabled": True,
            "duration_ms": 180,
            "easing": "out_cubic",
        },
    },
}


DEFAULT_SELECTOR_MAP: SelectorMap = {
    "root": [
        "QWidget",
        "QMainWindow",
        "QDialog",
    ],
    "panel": [
        "QFrame",
        "QGroupBox",
        "QDockWidget",
    ],
    "button": [
        "QPushButton",
        "QToolButton",
        "QCommandLinkButton",
    ],
    "input": [
        "QLineEdit",
        "QTextEdit",
        "QPlainTextEdit",
        "QSpinBox",
        "QDoubleSpinBox",
        "QComboBox",
        "QAbstractSpinBox",
    ],
    "item_views": [
        "QListView",
        "QTreeView",
        "QTableView",
        "QListWidget",
        "QTreeWidget",
        "QTableWidget",
    ],
    "menu": [
        "QMenu",
        "QMenuBar",
    ],
    "tooltip": [
        "QToolTip",
    ],
    "sidebar": [
        "QTabWidget::pane",
        "QToolBox",
    ],
    "statusbar": [
        "QStatusBar",
    ],
    "label": [
        "QLabel",
    ],
    "checkbox": [
        "QCheckBox",
        "QRadioButton",
    ],
    "scrollbar": [
        "QScrollBar",
    ],
    "progress": [
        "QProgressBar",
    ],
}


# ============================================================
# Modelos auxiliares
# ============================================================

@dataclass
class ThemeSnapshot:
    theme_id: str
    resolved_theme: ThemeDict


@dataclass
class TransitionConfig:
    enabled: bool = True
    duration_ms: int = 180
    easing: str = "out_cubic"


# ============================================================
# ThemeManager
# ============================================================

class ThemeManager(QObject):
    """
    Gestor central de estilos dinámicos para toda la aplicación Qt.

    Objetivos:
    - Cambios runtime de colores, fuentes y backgrounds
    - Integración transparente con ConfigManager
    - Temas predefinidos + custom
    - Generación QSS dinámica
    - Preparado para plugins visuales
    - Sin hojas de estilo monolíticas hardcodeadas
    """

    CONFIG_THEME_KEY = "theme"
    CONFIG_THEME_NAME_KEY = "theme.active"
    CONFIG_CUSTOM_THEMES_KEY = "theme.custom_themes"

    def __init__(
        self,
        app: QApplication,
        config_manager: ConfigManager,
        builtin_themes: Optional[Dict[str, ThemeDict]] = None,
        selector_map: Optional[SelectorMap] = None,
    ) -> None:
        super().__init__()
        self._app = app
        self._config = config_manager
        self._builtin_themes: Dict[str, ThemeDict] = _deep_copy(builtin_themes or BUILTIN_THEMES)
        self._selector_map: SelectorMap = _deep_copy(selector_map or DEFAULT_SELECTOR_MAP)
        self._plugins: List[ThemePlugin] = []
        self._custom_themes: Dict[str, ThemeDict] = self._load_custom_themes()
        self._runtime_overrides: ThemeDict = {}
        self._animations: List[QPropertyAnimation] = []
        self._background_widgets: List[QWidget] = []

        self._ensure_config_schema()

    # --------------------------------------------------------
    # API pública principal
    # --------------------------------------------------------

    def apply_current_theme(self) -> None:
        """
        Aplica el tema activo almacenado en configuración.
        """
        theme_name = self.get_active_theme_name()
        snapshot = self.resolve_theme(theme_name)
        self.apply_theme_snapshot(snapshot)

    def set_theme(self, theme_name: str) -> None:
        """
        Establece y aplica un tema predefinido o custom.
        """
        if not self.theme_exists(theme_name):
            raise ValueError(f"Tema no encontrado: {theme_name!r}")

        self._config.set(self.CONFIG_THEME_NAME_KEY, theme_name)
        snapshot = self.resolve_theme(theme_name)
        self.apply_theme_snapshot(snapshot)

    def register_custom_theme(self, theme_name: str, theme_data: ThemeDict, persist: bool = True) -> None:
        """
        Registra un tema custom escalable.
        """
        if not isinstance(theme_name, str) or not theme_name.strip():
            raise ValueError("theme_name debe ser un string no vacío.")

        normalized_name = theme_name.strip()
        self._custom_themes[normalized_name] = _deep_copy(theme_data)

        if persist:
            self._persist_custom_themes()

    def remove_custom_theme(self, theme_name: str) -> None:
        """
        Elimina un tema custom. No elimina temas builtin.
        """
        if theme_name in self._builtin_themes:
            raise ValueError("No se puede eliminar un tema predefinido.")

        if theme_name in self._custom_themes:
            del self._custom_themes[theme_name]
            self._persist_custom_themes()

    def list_themes(self) -> List[str]:
        """
        Devuelve la lista completa de temas disponibles.
        """
        names = set(self._builtin_themes.keys()) | set(self._custom_themes.keys())
        return sorted(names)

    def theme_exists(self, theme_name: str) -> bool:
        return theme_name in self._builtin_themes or theme_name in self._custom_themes

    def get_active_theme_name(self) -> str:
        active = self._config.get(self.CONFIG_THEME_NAME_KEY, "dark")
        return active if self.theme_exists(active) else "dark"

    def get_resolved_theme(self) -> ThemeDict:
        return self.resolve_theme(self.get_active_theme_name()).resolved_theme

    # --------------------------------------------------------
    # Cambios runtime
    # --------------------------------------------------------

    def set_runtime_color(self, token_name: str, color_hex: str, auto_apply: bool = True) -> None:
        """
        Sobrescribe un color del tema activo en runtime.
        """
        self._runtime_overrides.setdefault("ui", {}).setdefault("colors", {})[token_name] = color_hex
        self._config.set(f"ui.colors.{token_name}", color_hex)
        if auto_apply:
            self.apply_current_theme()

    def set_runtime_font(
        self,
        *,
        family: Optional[str] = None,
        size: Optional[int] = None,
        weight: Optional[str] = None,
        ui_family: Optional[str] = None,
        ui_size: Optional[int] = None,
        title_family: Optional[str] = None,
        title_size: Optional[int] = None,
        mono_family: Optional[str] = None,
        mono_size: Optional[int] = None,
        auto_apply: bool = True,
    ) -> None:
        patch: ThemeDict = {"ui": {"fonts": {}}}
        fonts = patch["ui"]["fonts"]

        if family is not None:
            fonts["family"] = family
        if size is not None:
            fonts["size"] = size
        if weight is not None:
            fonts["weight"] = weight
        if ui_family is not None:
            fonts["ui_family"] = ui_family
        if ui_size is not None:
            fonts["ui_size"] = ui_size
        if title_family is not None:
            fonts["title_family"] = title_family
        if title_size is not None:
            fonts["title_size"] = title_size
        if mono_family is not None:
            fonts["mono_family"] = mono_family
        if mono_size is not None:
            fonts["mono_size"] = mono_size

        self._runtime_overrides = _deep_merge(self._runtime_overrides, patch)
        self._config.set_font(
            family=family,
            size=size,
            weight=weight,
            ui_family=ui_family,
            ui_size=ui_size,
            title_family=title_family,
            title_size=title_size,
            mono_family=mono_family,
            mono_size=mono_size,
        )
        if auto_apply:
            self.apply_current_theme()

    def set_runtime_background(
        self,
        *,
        enabled: Optional[bool] = None,
        image_path: Optional[str] = None,
        opacity: Optional[float] = None,
        mode: Optional[str] = None,
        auto_apply: bool = True,
    ) -> None:
        patch: ThemeDict = {"ui": {"backgrounds": {}}}
        bg = patch["ui"]["backgrounds"]

        if enabled is not None:
            bg["enabled"] = enabled
        if image_path is not None:
            bg["image_path"] = image_path
        if opacity is not None:
            bg["opacity"] = opacity
        if mode is not None:
            bg["mode"] = mode

        self._runtime_overrides = _deep_merge(self._runtime_overrides, patch)
        self._config.configure_background(
            enabled=enabled,
            image_path=image_path,
            opacity=opacity,
            mode=mode,
        )
        if auto_apply:
            self.apply_current_theme()

    def clear_runtime_overrides(self, auto_apply: bool = True) -> None:
        self._runtime_overrides = {}
        if auto_apply:
            self.apply_current_theme()

    # --------------------------------------------------------
    # Plugins y extensiones
    # --------------------------------------------------------

    def register_plugin(self, plugin: ThemePlugin) -> None:
        self._plugins.append(plugin)
        selectors = plugin.contribute_selectors()
        if selectors:
            self._selector_map = self._merge_selector_maps(self._selector_map, selectors)

    def register_selector_group(self, group_name: str, selectors: Sequence[str]) -> None:
        existing = list(self._selector_map.get(group_name, []))
        for selector in selectors:
            if selector not in existing:
                existing.append(selector)
        self._selector_map[group_name] = existing

    # --------------------------------------------------------
    # Resolución de tema
    # --------------------------------------------------------

    def resolve_theme(self, theme_name: str) -> ThemeSnapshot:
        """
        Resuelve tema final combinando:
        - tema builtin o custom
        - configuración persistida en ConfigManager
        - overrides runtime
        - extensiones de plugins
        """
        if theme_name in self._custom_themes:
            base_theme = _deep_copy(self._custom_themes[theme_name])
        elif theme_name in self._builtin_themes:
            base_theme = _deep_copy(self._builtin_themes[theme_name])
        else:
            base_theme = _deep_copy(self._builtin_themes["dark"])

        config_overlay = {
            "ui": {
                "colors": self._config.get("ui.colors", {}),
                "fonts": self._config.get("ui.fonts", {}),
                "backgrounds": self._config.get("ui.backgrounds", {}),
            },
            "animations": self._build_animation_overlay(),
        }

        resolved = _deep_merge(base_theme, config_overlay)
        resolved = _deep_merge(resolved, self._runtime_overrides)

        for plugin in self._plugins:
            resolved = plugin.extend_theme(resolved)

        resolved = self._normalize_theme(resolved, fallback=base_theme)
        return ThemeSnapshot(theme_id=theme_name, resolved_theme=resolved)

    def apply_theme_snapshot(self, snapshot: ThemeSnapshot) -> None:
        """
        Aplica palette, fuente global, QSS dinámico y fondos.
        """
        theme = snapshot.resolved_theme

        self._apply_palette(theme)
        self._apply_global_font(theme)
        self._apply_dynamic_qss(theme)
        self._apply_backgrounds(theme)
        self._apply_widget_transitions(theme)

    # --------------------------------------------------------
    # Aplicación de estilo
    # --------------------------------------------------------

    def _apply_palette(self, theme: ThemeDict) -> None:
        colors = theme["ui"]["colors"]
        palette = self._app.palette()

        palette.setColor(QPalette.Window, QColor(colors["window_bg"]))
        palette.setColor(QPalette.WindowText, QColor(colors["window_fg"]))
        palette.setColor(QPalette.Base, QColor(colors["input_bg"]))
        palette.setColor(QPalette.AlternateBase, QColor(colors["panel_bg"]))
        palette.setColor(QPalette.Text, QColor(colors["input_fg"]))
        palette.setColor(QPalette.Button, QColor(colors["button_bg"]))
        palette.setColor(QPalette.ButtonText, QColor(colors["button_fg"]))
        palette.setColor(QPalette.Highlight, QColor(colors["selection_bg"]))
        palette.setColor(QPalette.HighlightedText, QColor(colors["selection_fg"]))
        palette.setColor(QPalette.ToolTipBase, QColor(colors["tooltip_bg"]))
        palette.setColor(QPalette.ToolTipText, QColor(colors["tooltip_fg"]))

        self._app.setPalette(palette)

    def _apply_global_font(self, theme: ThemeDict) -> None:
        fonts = theme["ui"]["fonts"]
        app_font = QFont(fonts["ui_family"], int(fonts["ui_size"]))
        weight_name = fonts.get("weight", "normal")

        if hasattr(QFont, "Weight"):  # Qt6
            weight_map = {
                "light": QFont.Weight.Light,
                "normal": QFont.Weight.Normal,
                "medium": QFont.Weight.Medium,
                "bold": QFont.Weight.Bold,
            }
            app_font.setWeight(weight_map.get(weight_name, QFont.Weight.Normal))
        else:  # Qt5
            weight_map = {
                "light": 25,
                "normal": 50,
                "medium": 57,
                "bold": 75,
            }
            app_font.setWeight(weight_map.get(weight_name, 50))

        self._app.setFont(app_font)

    def _apply_dynamic_qss(self, theme: ThemeDict) -> None:
        qss = self.build_stylesheet(theme)
        self._app.setStyleSheet(qss)

    def _apply_backgrounds(self, theme: ThemeDict) -> None:
        bg = theme["ui"]["backgrounds"]
        if not bg.get("enabled", False):
            self._clear_background_styles()
            return

        image_path = _normalize_path(bg.get("image_path", ""))
        if not image_path or not Path(image_path).exists():
            self._clear_background_styles()
            return

        mode = bg.get("mode", "cover")
        qss_mode = self._background_mode_to_qss(mode)

        background_rule = (
            "background-image: url("
            f"{_qss_quote(image_path)}"
            ");"
            f"background-repeat: no-repeat;"
            f"background-position: center;"
            f"{qss_mode}"
        )

        for widget in self._iter_top_level_widgets():
            existing = widget.styleSheet() or ""
            if "/* THEME_MANAGER_BACKGROUND */" in existing:
                prefix = existing.split("/* THEME_MANAGER_BACKGROUND */")[0].rstrip()
            else:
                prefix = existing.rstrip()

            widget.setStyleSheet(
                (
                    f"{prefix}\n"
                    "/* THEME_MANAGER_BACKGROUND */\n"
                    f"QWidget {{ {background_rule} }}\n"
                ).strip()
            )

    def _clear_background_styles(self) -> None:
        for widget in self._iter_top_level_widgets():
            existing = widget.styleSheet() or ""
            if "/* THEME_MANAGER_BACKGROUND */" in existing:
                cleaned = existing.split("/* THEME_MANAGER_BACKGROUND */")[0].rstrip()
                widget.setStyleSheet(cleaned)

    def _apply_widget_transitions(self, theme: ThemeDict) -> None:
        animations = theme.get("animations", {})
        if not animations.get("enabled", True):
            self._animations.clear()
            return

        duration = int(animations.get("duration_ms", 180))
        easing = self._resolve_easing_curve(animations.get("easing", "out_cubic"))

        self._animations.clear()
        for widget in self._iter_top_level_widgets():
            if not widget.isVisible():
                continue

            animation = QPropertyAnimation(widget, b"windowOpacity", self)
            animation.setDuration(duration)
            animation.setStartValue(max(0.90, float(widget.windowOpacity()) if hasattr(widget, "windowOpacity") else 1.0))
            animation.setEndValue(1.0)
            animation.setEasingCurve(easing)

            try:
                animation.start()
                self._animations.append(animation)
            except Exception:
                continue

    # --------------------------------------------------------
    # Generación de QSS
    # --------------------------------------------------------

    def build_stylesheet(self, theme: ThemeDict) -> str:
        """
        Construye una hoja QSS dinámica a partir de tokens.
        """
        colors = theme["ui"]["colors"]
        fonts = theme["ui"]["fonts"]
        bg = theme["ui"]["backgrounds"]

        rules: List[str] = []

        root_props = {
            "background-color": colors["window_bg"],
            "color": colors["window_fg"],
            **_font_to_qss(fonts["ui_family"], int(fonts["ui_size"]), fonts.get("weight", "normal")),
        }
        rules.extend(self._build_group_rules("root", root_props))

        panel_props = {
            "background-color": colors["panel_bg"],
            "color": colors["panel_fg"],
            "border": f"1px solid {colors['input_border']}",
        }
        rules.extend(self._build_group_rules("panel", panel_props))

        button_props = {
            "background-color": colors["button_bg"],
            "color": colors["button_fg"],
            "border": f"1px solid {colors['input_border']}",
            "padding": "6px 10px",
        }
        button_hover_props = {
            "background-color": colors["button_hover_bg"],
        }
        rules.extend(self._build_group_rules("button", button_props))
        rules.extend(self._build_group_rules("button", button_hover_props, pseudo="hover"))

        input_props = {
            "background-color": colors["input_bg"],
            "color": colors["input_fg"],
            "border": f"1px solid {colors['input_border']}",
            "selection-background-color": colors["selection_bg"],
            "selection-color": colors["selection_fg"],
            "padding": "4px 6px",
        }
        rules.extend(self._build_group_rules("input", input_props))

        item_view_props = {
            "background-color": colors["panel_bg"],
            "color": colors["panel_fg"],
            "alternate-background-color": colors["window_bg"],
            "selection-background-color": colors["selection_bg"],
            "selection-color": colors["selection_fg"],
            "border": f"1px solid {colors['input_border']}",
        }
        rules.extend(self._build_group_rules("item_views", item_view_props))

        tooltip_props = {
            "background-color": colors["tooltip_bg"],
            "color": colors["tooltip_fg"],
            "border": f"1px solid {colors['input_border']}",
            "padding": "4px",
        }
        rules.extend(self._build_group_rules("tooltip", tooltip_props))

        menu_props = {
            "background-color": colors["menu_bg"],
            "color": colors["menu_fg"],
            "border": f"1px solid {colors['input_border']}",
        }
        rules.extend(self._build_group_rules("menu", menu_props))

        sidebar_props = {
            "background-color": colors["sidebar_bg"],
            "color": colors["sidebar_fg"],
        }
        rules.extend(self._build_group_rules("sidebar", sidebar_props))

        statusbar_props = {
            "background-color": colors["statusbar_bg"],
            "color": colors["statusbar_fg"],
        }
        rules.extend(self._build_group_rules("statusbar", statusbar_props))

        label_props = {
            "color": colors["window_fg"],
        }
        rules.extend(self._build_group_rules("label", label_props))

        checkbox_props = {
            "color": colors["window_fg"],
            "spacing": "6px",
        }
        rules.extend(self._build_group_rules("checkbox", checkbox_props))

        progress_props = {
            "background-color": colors["panel_bg"],
            "color": colors["panel_fg"],
            "border": f"1px solid {colors['input_border']}",
            "text-align": "center",
        }
        rules.extend(self._build_group_rules("progress", progress_props))
        rules.append(
            "QProgressBar::chunk { "
            f"background-color: {colors['accent']}; "
            "}"
        )

        scrollbar_props = {
            "background-color": colors["panel_bg"],
            "border": "none",
        }
        rules.extend(self._build_group_rules("scrollbar", scrollbar_props))
        rules.append(
            "QScrollBar::handle { "
            f"background-color: {colors['button_bg']}; "
            "min-height: 20px; "
            "border-radius: 4px; "
            "}"
        )
        rules.append(
            "QScrollBar::handle:hover { "
            f"background-color: {colors['button_hover_bg']}; "
            "}"
        )

        if bg.get("enabled") and bg.get("image_path"):
            image_path = _normalize_path(bg["image_path"])
            if image_path:
                rules.append(self._build_background_rule(image_path, bg.get("mode", "cover")))

        for plugin in self._plugins:
            rules.extend(plugin.contribute_qss_rules(theme))

        return "\n".join(rule for rule in rules if rule.strip())

    def _build_group_rules(
        self,
        group_name: str,
        properties: Dict[str, str],
        pseudo: Optional[str] = None,
    ) -> List[str]:
        selectors = self._selector_map.get(group_name, [])
        if not selectors or not properties:
            return []

        suffix = f":{pseudo}" if pseudo else ""
        prop_str = self._serialize_qss_properties(properties)

        return [f"{selector}{suffix} {{ {prop_str} }}" for selector in selectors]

    def _serialize_qss_properties(self, properties: Dict[str, Any]) -> str:
        chunks = []
        for key, value in properties.items():
            prop_name = _to_kebab_case(key)
            chunks.append(f"{prop_name}: {value};")
        return " ".join(chunks)

    def _build_background_rule(self, image_path: str, mode: str) -> str:
        mode_qss = self._background_mode_to_qss(mode)
        return (
            "QWidget { "
            f"background-image: url({_qss_quote(image_path)}); "
            "background-position: center; "
            "background-repeat: no-repeat; "
            f"{mode_qss}"
            " }"
        )

    # --------------------------------------------------------
    # Helpers Qt
    # --------------------------------------------------------

    def _iter_top_level_widgets(self) -> List[QWidget]:
        widgets: List[QWidget] = []
        for widget in self._app.topLevelWidgets():
            if isinstance(widget, QWidget):
                widgets.append(widget)
        return widgets

    def _resolve_easing_curve(self, easing_name: str) -> QEasingCurve:
        easing_name = (easing_name or "out_cubic").lower()

        if hasattr(QEasingCurve, "Type"):  # Qt6
            mapping = {
                "linear": QEasingCurve.Type.Linear,
                "in_out_quad": QEasingCurve.Type.InOutQuad,
                "out_cubic": QEasingCurve.Type.OutCubic,
                "out_quart": QEasingCurve.Type.OutQuart,
                "out_expo": QEasingCurve.Type.OutExpo,
            }
            return QEasingCurve(mapping.get(easing_name, QEasingCurve.Type.OutCubic))

        mapping = {
            "linear": QEasingCurve.Linear,
            "in_out_quad": QEasingCurve.InOutQuad,
            "out_cubic": QEasingCurve.OutCubic,
            "out_quart": QEasingCurve.OutQuart,
            "out_expo": QEasingCurve.OutExpo,
        }
        return QEasingCurve(mapping.get(easing_name, QEasingCurve.OutCubic))

    def _background_mode_to_qss(self, mode: str) -> str:
        normalized = (mode or "cover").lower()
        # QSS no soporta todos los modos CSS de forma nativa.
        # Se deja preparado para extender con estrategias futuras por plugin.
        mapping = {
            "cover": "background-attachment: fixed;",
            "contain": "background-attachment: scroll;",
            "stretch": "background-attachment: fixed;",
            "center": "background-attachment: scroll;",
            "tile": "background-repeat: repeat;",
        }
        return mapping.get(normalized, "background-attachment: fixed;")

    # --------------------------------------------------------
    # Integración con config_manager
    # --------------------------------------------------------

    def _ensure_config_schema(self) -> None:
        """
        Asegura claves mínimas para el subsistema de temas.
        """
        self._config.register_defaults(
            {
                "theme": {
                    "active": "dark",
                    "custom_themes": {},
                }
            },
            apply_if_missing=True,
        )

    def _load_custom_themes(self) -> Dict[str, ThemeDict]:
        themes = self._config.get(self.CONFIG_CUSTOM_THEMES_KEY, {})
        return themes if isinstance(themes, dict) else {}

    def _persist_custom_themes(self) -> None:
        self._config.set(self.CONFIG_CUSTOM_THEMES_KEY, self._custom_themes)

    def _build_animation_overlay(self) -> Dict[str, Any]:
        enabled = self._config.get("animations.enabled", True)
        return {
            "enabled": enabled,
            "duration_ms": 180,
            "easing": "out_cubic",
        }

    # --------------------------------------------------------
    # Normalización
    # --------------------------------------------------------

    def _normalize_theme(self, theme: ThemeDict, fallback: ThemeDict) -> ThemeDict:
        """
        Normaliza estructura mínima de tema sin acoplarse a una plantilla rígida.
        """
        result = _deep_merge(fallback, theme)

        colors = result.setdefault("ui", {}).setdefault("colors", {})
        fallback_colors = fallback["ui"]["colors"]
        for key, fallback_value in fallback_colors.items():
            colors[key] = _ensure_hex_color(colors.get(key), fallback_value)

        fonts = result["ui"].setdefault("fonts", {})
        fallback_fonts = fallback["ui"]["fonts"]
        for key, fallback_value in fallback_fonts.items():
            value = fonts.get(key, fallback_value)
            if "size" in key:
                if not isinstance(value, int) or not (6 <= value <= 96):
                    value = fallback_value
            elif key == "weight":
                if value not in ("light", "normal", "medium", "bold"):
                    value = fallback_value
            else:
                if not isinstance(value, str) or not value.strip():
                    value = fallback_value
            fonts[key] = value

        backgrounds = result["ui"].setdefault("backgrounds", {})
        fallback_bg = fallback["ui"]["backgrounds"]

        enabled = backgrounds.get("enabled", fallback_bg.get("enabled", False))
        backgrounds["enabled"] = bool(enabled)

        image_path = backgrounds.get("image_path", fallback_bg.get("image_path", ""))
        backgrounds["image_path"] = _normalize_path(image_path)

        opacity = backgrounds.get("opacity", fallback_bg.get("opacity", 1.0))
        if not isinstance(opacity, (int, float)) or not (0.0 <= float(opacity) <= 1.0):
            opacity = fallback_bg.get("opacity", 1.0)
        backgrounds["opacity"] = float(opacity)

        mode = backgrounds.get("mode", fallback_bg.get("mode", "cover"))
        if mode not in ("cover", "contain", "stretch", "center", "tile"):
            mode = fallback_bg.get("mode", "cover")
        backgrounds["mode"] = mode

        animations = result.setdefault("animations", {})
        fallback_anim = fallback.get("animations", {})
        anim_enabled = animations.get("enabled", fallback_anim.get("enabled", True))
        animations["enabled"] = bool(anim_enabled)

        duration = animations.get("duration_ms", fallback_anim.get("duration_ms", 180))
        if not isinstance(duration, int) or duration < 0 or duration > 5000:
            duration = fallback_anim.get("duration_ms", 180)
        animations["duration_ms"] = duration

        easing = animations.get("easing", fallback_anim.get("easing", "out_cubic"))
        if easing not in ("linear", "in_out_quad", "out_cubic", "out_quart", "out_expo"):
            easing = fallback_anim.get("easing", "out_cubic")
        animations["easing"] = easing

        meta = result.setdefault("meta", {})
        meta.setdefault("name", fallback.get("meta", {}).get("name", "custom"))
        meta.setdefault("label", fallback.get("meta", {}).get("label", meta["name"]))

        return result

    def _merge_selector_maps(self, base: SelectorMap, override: SelectorMap) -> SelectorMap:
        merged = _deep_copy(base)
        for group_name, selectors in override.items():
            existing = list(merged.get(group_name, []))
            for selector in selectors:
                if selector not in existing:
                    existing.append(selector)
            merged[group_name] = existing
        return merged


__all__ = [
    "ThemeManager",
    "ThemePlugin",
    "ThemeSnapshot",
    "TransitionConfig",
    "BUILTIN_THEMES",
    "DEFAULT_SELECTOR_MAP",
]