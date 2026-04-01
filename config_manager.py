"""
config_manager.py

Gestor centralizado y thread-safe para la configuración de usuario.

Características:
- Persistencia en JSON o SQLite
- Carga y guardado automático
- Validación de datos
- Defaults robustos
- Escalable para nuevas opciones
- API desacoplada y documentada

Uso rápido:
    manager = ConfigManager(storage_backend="json", storage_path="user_config.json")
    theme = manager.get("ui.colors")
    manager.set("animations.enabled", False)
    manager.add_favorite("/home/user/project")
    manager.add_recent_path("/home/user/project/file.txt")
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


JsonDict = Dict[str, Any]


# ============================================================
# Defaults
# ============================================================

DEFAULT_CONFIG: JsonDict = {
    "ui": {
        "colors": {
            "window_bg": "#1E1E1E",
            "window_fg": "#F5F5F5",
            "panel_bg": "#252526",
            "panel_fg": "#CCCCCC",
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
            "size": 11,
            "weight": "normal",
            "ui_family": "Segoe UI",
            "ui_size": 11,
            "title_family": "Segoe UI Semibold",
            "title_size": 13,
            "mono_family": "Consolas",
            "mono_size": 11,
        },
        "backgrounds": {
            "enabled": False,
            "image_path": "",
            "opacity": 1.0,
            "mode": "cover",  # cover | contain | stretch | center | tile
        },
    },
    "animations": {
        "enabled": True,
    },
    "history": {
        "recent_paths": [],
        "max_recent_paths": 20,
    },
    "favorites": {
        "items": [],
        "max_items": 100,
    },
    "meta": {
        "version": 1,
    },
}


# ============================================================
# Validation
# ============================================================

class ConfigValidationError(ValueError):
    """Error lanzado cuando la configuración no cumple las reglas esperadas."""


def _deep_copy(data: Any) -> Any:
    return copy.deepcopy(data)


def _deep_merge(base: JsonDict, override: JsonDict) -> JsonDict:
    """
    Fusiona recursivamente override sobre base, sin mutar los argumentos.
    """
    result = _deep_copy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = _deep_copy(value)
    return result


def _is_hex_color(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if len(value) not in (7, 9):
        return False
    if not value.startswith("#"):
        return False
    hex_part = value[1:]
    return all(ch in "0123456789abcdefABCDEF" for ch in hex_part)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _validate_choice(value: Any, allowed: Sequence[str], field_name: str) -> None:
    if value not in allowed:
        raise ConfigValidationError(
            f"Valor inválido para '{field_name}': {value!r}. "
            f"Permitidos: {list(allowed)!r}"
        )


def _validate_int_range(value: Any, field_name: str, min_value: int, max_value: int) -> None:
    if not isinstance(value, int):
        raise ConfigValidationError(f"'{field_name}' debe ser int, recibido: {type(value).__name__}")
    if not (min_value <= value <= max_value):
        raise ConfigValidationError(
            f"'{field_name}' fuera de rango ({min_value}-{max_value}): {value}"
        )


def _validate_float_range(value: Any, field_name: str, min_value: float, max_value: float) -> None:
    if not isinstance(value, (int, float)):
        raise ConfigValidationError(f"'{field_name}' debe ser float, recibido: {type(value).__name__}")
    if not (min_value <= float(value) <= max_value):
        raise ConfigValidationError(
            f"'{field_name}' fuera de rango ({min_value}-{max_value}): {value}"
        )


def _validate_bool(value: Any, field_name: str) -> None:
    if not isinstance(value, bool):
        raise ConfigValidationError(f"'{field_name}' debe ser bool, recibido: {type(value).__name__}")


def _normalize_path(path_value: str) -> str:
    return str(Path(path_value).expanduser())


def validate_config(config: JsonDict) -> None:
    """
    Valida la estructura completa de configuración.
    Lanza ConfigValidationError si algún dato es inválido.
    """
    if not isinstance(config, dict):
        raise ConfigValidationError("La configuración raíz debe ser un diccionario.")

    ui = config.get("ui", {})
    colors = ui.get("colors", {})
    fonts = ui.get("fonts", {})
    backgrounds = ui.get("backgrounds", {})
    animations = config.get("animations", {})
    history = config.get("history", {})
    favorites = config.get("favorites", {})
    meta = config.get("meta", {})

    if not isinstance(colors, dict):
        raise ConfigValidationError("'ui.colors' debe ser un diccionario.")
    for key, value in colors.items():
        if not _is_hex_color(value):
            raise ConfigValidationError(
                f"Color inválido en 'ui.colors.{key}': {value!r}. "
                "Se espera formato #RRGGBB o #RRGGBBAA."
            )

    if not isinstance(fonts, dict):
        raise ConfigValidationError("'ui.fonts' debe ser un diccionario.")

    for field in ("family", "ui_family", "title_family", "mono_family"):
        if field in fonts and not _is_non_empty_string(fonts[field]):
            raise ConfigValidationError(f"'ui.fonts.{field}' debe ser un string no vacío.")

    for field in ("size", "ui_size", "title_size", "mono_size"):
        if field in fonts:
            _validate_int_range(fonts[field], f"ui.fonts.{field}", 6, 96)

    if "weight" in fonts:
        _validate_choice(fonts["weight"], ("light", "normal", "medium", "bold"), "ui.fonts.weight")

    if not isinstance(backgrounds, dict):
        raise ConfigValidationError("'ui.backgrounds' debe ser un diccionario.")

    if "enabled" in backgrounds:
        _validate_bool(backgrounds["enabled"], "ui.backgrounds.enabled")

    if "image_path" in backgrounds and not isinstance(backgrounds["image_path"], str):
        raise ConfigValidationError("'ui.backgrounds.image_path' debe ser string.")

    if "opacity" in backgrounds:
        _validate_float_range(backgrounds["opacity"], "ui.backgrounds.opacity", 0.0, 1.0)

    if "mode" in backgrounds:
        _validate_choice(
            backgrounds["mode"],
            ("cover", "contain", "stretch", "center", "tile"),
            "ui.backgrounds.mode",
        )

    if not isinstance(animations, dict):
        raise ConfigValidationError("'animations' debe ser un diccionario.")
    if "enabled" in animations:
        _validate_bool(animations["enabled"], "animations.enabled")

    if not isinstance(history, dict):
        raise ConfigValidationError("'history' debe ser un diccionario.")
    if "recent_paths" in history:
        if not isinstance(history["recent_paths"], list):
            raise ConfigValidationError("'history.recent_paths' debe ser una lista.")
        for item in history["recent_paths"]:
            if not isinstance(item, str):
                raise ConfigValidationError("Todos los elementos de 'history.recent_paths' deben ser strings.")
    if "max_recent_paths" in history:
        _validate_int_range(history["max_recent_paths"], "history.max_recent_paths", 1, 1000)

    if not isinstance(favorites, dict):
        raise ConfigValidationError("'favorites' debe ser un diccionario.")
    if "items" in favorites:
        if not isinstance(favorites["items"], list):
            raise ConfigValidationError("'favorites.items' debe ser una lista.")
        for item in favorites["items"]:
            if not isinstance(item, str):
                raise ConfigValidationError("Todos los elementos de 'favorites.items' deben ser strings.")
    if "max_items" in favorites:
        _validate_int_range(favorites["max_items"], "favorites.max_items", 1, 10000)

    if not isinstance(meta, dict):
        raise ConfigValidationError("'meta' debe ser un diccionario.")
    if "version" in meta:
        _validate_int_range(meta["version"], "meta.version", 1, 999999)


def normalize_config(config: JsonDict) -> JsonDict:
    """
    Normaliza ciertos valores para mantener consistencia interna.
    """
    normalized = _deep_copy(config)

    bg_path = normalized["ui"]["backgrounds"].get("image_path", "")
    if isinstance(bg_path, str) and bg_path.strip():
        normalized["ui"]["backgrounds"]["image_path"] = _normalize_path(bg_path)

    recents = normalized["history"].get("recent_paths", [])
    normalized["history"]["recent_paths"] = _normalize_string_list_unique(recents)

    favs = normalized["favorites"].get("items", [])
    normalized["favorites"]["items"] = _normalize_string_list_unique(favs)

    max_recent = normalized["history"]["max_recent_paths"]
    normalized["history"]["recent_paths"] = normalized["history"]["recent_paths"][:max_recent]

    max_items = normalized["favorites"]["max_items"]
    normalized["favorites"]["items"] = normalized["favorites"]["items"][:max_items]

    return normalized


def _normalize_string_list_unique(items: Sequence[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value:
            continue
        value = _normalize_path(value)
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


# ============================================================
# Storage Backends
# ============================================================

class ConfigStorage(ABC):
    """
    Abstracción de persistencia.
    Permite agregar nuevos backends sin modificar ConfigManager.
    """

    @abstractmethod
    def load(self) -> JsonDict:
        raise NotImplementedError

    @abstractmethod
    def save(self, config: JsonDict) -> None:
        raise NotImplementedError


@dataclass
class JsonConfigStorage(ConfigStorage):
    path: str

    def load(self) -> JsonDict:
        file_path = Path(self.path)
        if not file_path.exists():
            return _deep_copy(DEFAULT_CONFIG)

        try:
            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return _deep_copy(DEFAULT_CONFIG)
            return data
        except (json.JSONDecodeError, OSError):
            return _deep_copy(DEFAULT_CONFIG)

    def save(self, config: JsonDict) -> None:
        file_path = Path(self.path)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2, sort_keys=True)

        os.replace(temp_path, file_path)


@dataclass
class SQLiteConfigStorage(ConfigStorage):
    path: str
    table_name: str = "app_config"

    def _connect(self) -> sqlite3.Connection:
        db_path = Path(self.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                config_json TEXT NOT NULL
            )
            """
        )
        return conn

    def load(self) -> JsonDict:
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    f"SELECT config_json FROM {self.table_name} WHERE id = 1"
                )
                row = cursor.fetchone()
                if not row:
                    return _deep_copy(DEFAULT_CONFIG)
                data = json.loads(row[0])
                if not isinstance(data, dict):
                    return _deep_copy(DEFAULT_CONFIG)
                return data
        except (sqlite3.Error, json.JSONDecodeError, OSError):
            return _deep_copy(DEFAULT_CONFIG)

    def save(self, config: JsonDict) -> None:
        config_json = json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table_name} (id, config_json)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET config_json = excluded.config_json
                """,
                (config_json,),
            )
            conn.commit()


# ============================================================
# ConfigManager
# ============================================================

class ConfigManager:
    """
    Gestor central de configuración.

    Objetivos:
    - Thread-safe
    - Persistencia automática
    - Validación consistente
    - Extensible para nuevos bloques de configuración

    Parámetros:
        storage_backend: "json" o "sqlite"
        storage_path: ruta del archivo JSON o SQLite
        auto_save: guardar automáticamente tras cambios
        defaults: configuración base opcional
    """

    def __init__(
        self,
        storage_backend: str = "json",
        storage_path: str = "config.json",
        auto_save: bool = True,
        defaults: Optional[JsonDict] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._auto_save = auto_save
        self._defaults = _deep_merge(DEFAULT_CONFIG, defaults or {})
        validate_config(self._defaults)

        self._storage = self._build_storage(storage_backend, storage_path)
        self._config = self._load_and_repair()

    # --------------------------------------------------------
    # Inicialización interna
    # --------------------------------------------------------

    def _build_storage(self, backend: str, path: str) -> ConfigStorage:
        backend_normalized = backend.strip().lower()
        if backend_normalized == "json":
            return JsonConfigStorage(path)
        if backend_normalized == "sqlite":
            return SQLiteConfigStorage(path)
        raise ValueError(f"Backend no soportado: {backend!r}. Use 'json' o 'sqlite'.")

    def _load_and_repair(self) -> JsonDict:
        with self._lock:
            loaded = self._storage.load()
            if not isinstance(loaded, dict):
                loaded = {}

            merged = _deep_merge(self._defaults, loaded)

            try:
                validate_config(merged)
                normalized = normalize_config(merged)
            except ConfigValidationError:
                normalized = _deep_copy(self._defaults)

            if self._auto_save:
                self._storage.save(normalized)

            return normalized

    def _save_if_needed(self) -> None:
        if self._auto_save:
            self._storage.save(self._config)

    # --------------------------------------------------------
    # Acceso general
    # --------------------------------------------------------

    def get_all(self) -> JsonDict:
        """Devuelve una copia profunda de toda la configuración."""
        with self._lock:
            return _deep_copy(self._config)

    def reload(self) -> JsonDict:
        """Recarga desde el backend y devuelve la configuración actual."""
        with self._lock:
            self._config = self._load_and_repair()
            return _deep_copy(self._config)

    def save(self) -> None:
        """Fuerza persistencia inmediata."""
        with self._lock:
            validate_config(self._config)
            self._config = normalize_config(self._config)
            self._storage.save(self._config)

    def reset(self) -> None:
        """Restablece toda la configuración a defaults."""
        with self._lock:
            self._config = _deep_copy(self._defaults)
            self._save_if_needed()

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Obtiene un valor mediante ruta con puntos.
        Ejemplo: 'ui.colors.button_bg'
        """
        with self._lock:
            current: Any = self._config
            for part in key_path.split("."):
                if not isinstance(current, dict) or part not in current:
                    return default
                current = current[part]
            return _deep_copy(current)

    def set(self, key_path: str, value: Any) -> None:
        """
        Establece un valor usando ruta con puntos y valida el resultado final.
        """
        with self._lock:
            updated = _deep_copy(self._config)
            parts = key_path.split(".")
            target = updated

            for part in parts[:-1]:
                if part not in target or not isinstance(target[part], dict):
                    target[part] = {}
                target = target[part]

            target[parts[-1]] = value
            candidate = _deep_merge(self._defaults, updated)
            validate_config(candidate)
            self._config = normalize_config(candidate)
            self._save_if_needed()

    def update(self, partial_config: JsonDict) -> None:
        """
        Fusiona una configuración parcial, valida y persiste.
        """
        with self._lock:
            candidate = _deep_merge(self._config, partial_config)
            candidate = _deep_merge(self._defaults, candidate)
            validate_config(candidate)
            self._config = normalize_config(candidate)
            self._save_if_needed()

    def has(self, key_path: str) -> bool:
        """Indica si una ruta existe en la configuración."""
        sentinel = object()
        return self.get(key_path, sentinel) is not sentinel

    # --------------------------------------------------------
    # Operaciones específicas de dominio
    # --------------------------------------------------------

    def set_ui_color(self, component_name: str, color_hex: str) -> None:
        self.set(f"ui.colors.{component_name}", color_hex)

    def get_ui_color(self, component_name: str, default: Optional[str] = None) -> Optional[str]:
        return self.get(f"ui.colors.{component_name}", default)

    def set_font(
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
    ) -> None:
        patch: JsonDict = {"ui": {"fonts": {}}}
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

        self.update(patch)

    def configure_background(
        self,
        *,
        enabled: Optional[bool] = None,
        image_path: Optional[str] = None,
        opacity: Optional[float] = None,
        mode: Optional[str] = None,
    ) -> None:
        patch: JsonDict = {"ui": {"backgrounds": {}}}
        bg = patch["ui"]["backgrounds"]

        if enabled is not None:
            bg["enabled"] = enabled
        if image_path is not None:
            bg["image_path"] = image_path
        if opacity is not None:
            bg["opacity"] = opacity
        if mode is not None:
            bg["mode"] = mode

        self.update(patch)

    def set_animations_enabled(self, enabled: bool) -> None:
        self.set("animations.enabled", enabled)

    def add_recent_path(self, path_value: str) -> None:
        with self._lock:
            normalized = _normalize_path(path_value)
            recent_paths = self.get("history.recent_paths", [])
            max_items = self.get("history.max_recent_paths", 20)

            new_list = [p for p in recent_paths if p != normalized]
            new_list.insert(0, normalized)
            new_list = new_list[:max_items]

            self.set("history.recent_paths", new_list)

    def remove_recent_path(self, path_value: str) -> None:
        with self._lock:
            normalized = _normalize_path(path_value)
            recent_paths = self.get("history.recent_paths", [])
            new_list = [p for p in recent_paths if p != normalized]
            self.set("history.recent_paths", new_list)

    def clear_recent_paths(self) -> None:
        self.set("history.recent_paths", [])

    def get_recent_paths(self) -> List[str]:
        return self.get("history.recent_paths", [])

    def add_favorite(self, path_value: str) -> None:
        with self._lock:
            normalized = _normalize_path(path_value)
            items = self.get("favorites.items", [])
            max_items = self.get("favorites.max_items", 100)

            new_items = [p for p in items if p != normalized]
            new_items.insert(0, normalized)
            new_items = new_items[:max_items]

            self.set("favorites.items", new_items)

    def remove_favorite(self, path_value: str) -> None:
        with self._lock:
            normalized = _normalize_path(path_value)
            items = self.get("favorites.items", [])
            new_items = [p for p in items if p != normalized]
            self.set("favorites.items", new_items)

    def clear_favorites(self) -> None:
        self.set("favorites.items", [])

    def get_favorites(self) -> List[str]:
        return self.get("favorites.items", [])

    # --------------------------------------------------------
    # Extensibilidad
    # --------------------------------------------------------

    def register_defaults(self, partial_defaults: JsonDict, apply_if_missing: bool = True) -> None:
        """
        Registra nuevos defaults de manera segura.

        Si apply_if_missing=True, fusiona los nuevos defaults con la config
        actual sin sobreescribir valores ya establecidos por el usuario.
        """
        with self._lock:
            merged_defaults = _deep_merge(self._defaults, partial_defaults)
            validate_config(merged_defaults)

            self._defaults = merged_defaults

            if apply_if_missing:
                self._config = _deep_merge(self._defaults, self._config)
                validate_config(self._config)
                self._config = normalize_config(self._config)
                self._save_if_needed()

    # --------------------------------------------------------
    # Utilidades
    # --------------------------------------------------------

    def export(self) -> JsonDict:
        """Alias explícito de get_all()."""
        return self.get_all()

    def import_config(self, config: JsonDict, merge: bool = True) -> None:
        """
        Importa configuración externa.
        - merge=True: fusiona sobre la actual
        - merge=False: reemplaza base lógica y completa con defaults
        """
        with self._lock:
            candidate = _deep_merge(self._config, config) if merge else _deep_merge(self._defaults, config)
            validate_config(candidate)
            self._config = normalize_config(candidate)
            self._save_if_needed()


__all__ = [
    "ConfigManager",
    "ConfigStorage",
    "JsonConfigStorage",
    "SQLiteConfigStorage",
    "ConfigValidationError",
    "DEFAULT_CONFIG",
    "validate_config",
    "normalize_config",
]