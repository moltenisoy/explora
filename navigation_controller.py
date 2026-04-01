from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any


logger = logging.getLogger(__name__)

if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


EventCallback = Callable[[dict], None]


@dataclass
class NavigationState:
    """
    Estado de navegación independiente por vista/panel/tab.
    """
    current_path: Optional[Path] = None
    back_stack: List[Path] = field(default_factory=list)
    forward_stack: List[Path] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "current_path": str(self.current_path) if self.current_path else None,
            "back_stack": [str(p) for p in self.back_stack],
            "forward_stack": [str(p) for p in self.forward_stack],
            "can_go_back": len(self.back_stack) > 0,
            "can_go_forward": len(self.forward_stack) > 0,
            "can_go_up": self.current_path is not None and self.current_path.parent != self.current_path,
        }


class NavigationController:
    """
    Controlador de navegación desacoplado de UI.

    Responsabilidades:
    - Mantener historial back/forward por vista/panel.
    - Resolver navegación jerárquica.
    - Sincronizar cambios de ruta con la UI mediante eventos.
    - Soportar múltiples vistas (ej. panel izquierdo/derecho, tabs futuras).

    Concepto:
    - Cada `view_id` mantiene un estado totalmente independiente.
    - Se emiten eventos para que la UI reaccione.
    - No contiene lógica visual.
    """

    EVENT_VIEW_CREATED = "view_created"
    EVENT_VIEW_REMOVED = "view_removed"
    EVENT_NAVIGATED = "navigated"
    EVENT_HISTORY_CHANGED = "history_changed"
    EVENT_PATH_SYNC_REQUESTED = "path_sync_requested"
    EVENT_INVALID_PATH = "invalid_path"
    EVENT_ACTIVE_VIEW_CHANGED = "active_view_changed"

    def __init__(self) -> None:
        self._views: Dict[str, NavigationState] = {}
        self._listeners: Dict[str, List[EventCallback]] = {}
        self._global_listeners: List[EventCallback] = []
        self._active_view_id: Optional[str] = None
        self._lock = threading.RLock()

    # =========================================================
    # Gestión de vistas / paneles
    # =========================================================

    def create_view(self, view_id: str, initial_path: Optional[str] = None) -> None:
        """
        Crea una vista/panel independiente.
        """
        with self._lock:
            if view_id in self._views:
                raise ValueError(f"La vista '{view_id}' ya existe.")

            state = NavigationState()
            self._views[view_id] = state

            if self._active_view_id is None:
                self._active_view_id = view_id

            logger.info("Vista creada: %s", view_id)
            self._emit(
                self.EVENT_VIEW_CREATED,
                {
                    "view_id": view_id,
                    "state": state.snapshot(),
                },
            )

        if initial_path is not None:
            self.navigate_to(view_id, initial_path, record_history=False)

    def remove_view(self, view_id: str) -> None:
        with self._lock:
            self._require_view(view_id)
            removed_state = self._views.pop(view_id)

            if self._active_view_id == view_id:
                self._active_view_id = next(iter(self._views), None)

            logger.info("Vista eliminada: %s", view_id)
            self._emit(
                self.EVENT_VIEW_REMOVED,
                {
                    "view_id": view_id,
                    "state": removed_state.snapshot(),
                    "new_active_view_id": self._active_view_id,
                },
            )

    def set_active_view(self, view_id: str) -> None:
        with self._lock:
            self._require_view(view_id)
            self._active_view_id = view_id
            logger.info("Vista activa cambiada: %s", view_id)
            self._emit(
                self.EVENT_ACTIVE_VIEW_CHANGED,
                {
                    "view_id": view_id,
                    "state": self._views[view_id].snapshot(),
                },
            )

    def get_active_view(self) -> Optional[str]:
        with self._lock:
            return self._active_view_id

    def list_views(self) -> List[str]:
        with self._lock:
            return list(self._views.keys())

    # =========================================================
    # Navegación principal
    # =========================================================

    def navigate_to(
        self,
        view_id: str,
        path: str,
        *,
        record_history: bool = True,
        clear_forward: bool = True,
        emit_sync_event: bool = True,
    ) -> str:
        """
        Navega a una ruta absoluta o relativa.

        - record_history=True:
            guarda el current_path anterior en back_stack.
        - clear_forward=True:
            limpia forward_stack cuando la navegación es "nueva".
        """
        normalized = self._normalize_path(path)

        with self._lock:
            state = self._get_state(view_id)

            if not normalized.exists() or not normalized.is_dir():
                logger.warning("Ruta inválida para navegación [%s]: %s", view_id, normalized)
                self._emit(
                    self.EVENT_INVALID_PATH,
                    {
                        "view_id": view_id,
                        "requested_path": str(normalized),
                        "reason": "path_not_found_or_not_directory",
                        "state": state.snapshot(),
                    },
                )
                raise FileNotFoundError(f"Ruta inválida: {normalized}")

            previous = state.current_path

            if previous and previous == normalized:
                logger.debug("Ruta ya actual en vista %s: %s", view_id, normalized)
                if emit_sync_event:
                    self._emit_path_sync(view_id)
                return str(normalized)

            if record_history and previous is not None:
                state.back_stack.append(previous)

            if clear_forward:
                state.forward_stack.clear()

            state.current_path = normalized

            logger.info(
                "Navegación realizada | view=%s | from=%s | to=%s",
                view_id,
                previous,
                normalized,
            )

            payload = {
                "view_id": view_id,
                "path": str(normalized),
                "previous_path": str(previous) if previous else None,
                "state": state.snapshot(),
            }

            self._emit(self.EVENT_NAVIGATED, payload)
            self._emit(self.EVENT_HISTORY_CHANGED, payload)

            if emit_sync_event:
                self._emit_path_sync(view_id)

            return str(normalized)

    def go_back(self, view_id: str) -> Optional[str]:
        with self._lock:
            state = self._get_state(view_id)
            if not state.back_stack:
                logger.debug("No hay historial back para %s", view_id)
                return None

            if state.current_path is not None:
                state.forward_stack.append(state.current_path)

            target = state.back_stack.pop()

        return self.navigate_to(
            view_id,
            str(target),
            record_history=False,
            clear_forward=False,
            emit_sync_event=True,
        )

    def go_forward(self, view_id: str) -> Optional[str]:
        with self._lock:
            state = self._get_state(view_id)
            if not state.forward_stack:
                logger.debug("No hay historial forward para %s", view_id)
                return None

            if state.current_path is not None:
                state.back_stack.append(state.current_path)

            target = state.forward_stack.pop()

        return self.navigate_to(
            view_id,
            str(target),
            record_history=False,
            clear_forward=False,
            emit_sync_event=True,
        )

    def go_up(self, view_id: str) -> Optional[str]:
        with self._lock:
            state = self._get_state(view_id)
            if state.current_path is None:
                return None

            current = state.current_path
            parent = current.parent

            if parent == current:
                logger.debug("Ya está en el nivel raíz para %s", view_id)
                return None

        return self.navigate_to(view_id, str(parent))

    def refresh(self, view_id: str) -> Optional[str]:
        """
        Reemite eventos de sincronización sin alterar historial.
        Útil para recarga de UI o contenido.
        """
        with self._lock:
            state = self._get_state(view_id)
            if state.current_path is None:
                return None

            payload = {
                "view_id": view_id,
                "path": str(state.current_path),
                "previous_path": str(state.current_path),
                "state": state.snapshot(),
                "refresh": True,
            }

            logger.info("Refresh de navegación en vista %s: %s", view_id, state.current_path)
            self._emit(self.EVENT_NAVIGATED, payload)
            self._emit(self.EVENT_HISTORY_CHANGED, payload)
            self._emit_path_sync(view_id)

            return str(state.current_path)

    # =========================================================
    # Sincronización con barra de ruta
    # =========================================================

    def sync_from_address_bar(self, view_id: str, raw_path: str) -> str:
        """
        Punto de entrada para rutas escritas/pegadas en la barra.
        La UI delega aquí la validación y navegación real.
        """
        logger.info("Sync desde barra de ruta | view=%s | raw=%s", view_id, raw_path)
        return self.navigate_to(view_id, raw_path)

    def request_path_sync(self, view_id: str) -> None:
        """
        Fuerza emisión de evento para que la UI sincronice la barra de ruta.
        """
        with self._lock:
            self._get_state(view_id)
            self._emit_path_sync(view_id)

    # =========================================================
    # Estado / consulta
    # =========================================================

    def get_current_path(self, view_id: str) -> Optional[str]:
        with self._lock:
            state = self._get_state(view_id)
            return str(state.current_path) if state.current_path else None

    def get_state_snapshot(self, view_id: str) -> dict:
        with self._lock:
            return self._get_state(view_id).snapshot()

    def get_all_states(self) -> Dict[str, dict]:
        with self._lock:
            return {view_id: state.snapshot() for view_id, state in self._views.items()}

    def can_go_back(self, view_id: str) -> bool:
        with self._lock:
            return len(self._get_state(view_id).back_stack) > 0

    def can_go_forward(self, view_id: str) -> bool:
        with self._lock:
            return len(self._get_state(view_id).forward_stack) > 0

    def can_go_up(self, view_id: str) -> bool:
        with self._lock:
            state = self._get_state(view_id)
            return (
                state.current_path is not None
                and state.current_path.parent != state.current_path
            )

    def clear_history(self, view_id: str) -> None:
        with self._lock:
            state = self._get_state(view_id)
            state.back_stack.clear()
            state.forward_stack.clear()

            logger.info("Historial limpiado para vista %s", view_id)
            self._emit(
                self.EVENT_HISTORY_CHANGED,
                {
                    "view_id": view_id,
                    "path": str(state.current_path) if state.current_path else None,
                    "previous_path": None,
                    "state": state.snapshot(),
                    "history_cleared": True,
                },
            )

    # =========================================================
    # Eventos
    # =========================================================

    def add_listener(self, event_name: str, callback: EventCallback) -> None:
        with self._lock:
            self._listeners.setdefault(event_name, []).append(callback)

    def remove_listener(self, event_name: str, callback: EventCallback) -> None:
        with self._lock:
            if event_name in self._listeners:
                self._listeners[event_name] = [
                    cb for cb in self._listeners[event_name] if cb != callback
                ]

    def add_global_listener(self, callback: EventCallback) -> None:
        with self._lock:
            self._global_listeners.append(callback)

    def remove_global_listener(self, callback: EventCallback) -> None:
        with self._lock:
            self._global_listeners = [
                cb for cb in self._global_listeners if cb != callback
            ]

    # =========================================================
    # Helpers internos
    # =========================================================

    def _emit(self, event_name: str, payload: dict) -> None:
        listeners = []
        global_listeners = []

        with self._lock:
            listeners = list(self._listeners.get(event_name, []))
            global_listeners = list(self._global_listeners)

        event = {
            "event": event_name,
            **payload,
        }

        for callback in listeners:
            try:
                callback(event)
            except Exception:
                logger.exception("Error en listener de evento '%s'", event_name)

        for callback in global_listeners:
            try:
                callback(event)
            except Exception:
                logger.exception("Error en global listener de evento '%s'", event_name)

    def _emit_path_sync(self, view_id: str) -> None:
        state = self._get_state(view_id)
        self._emit(
            self.EVENT_PATH_SYNC_REQUESTED,
            {
                "view_id": view_id,
                "path": str(state.current_path) if state.current_path else None,
                "state": state.snapshot(),
            },
        )

    def _require_view(self, view_id: str) -> None:
        if view_id not in self._views:
            raise KeyError(f"La vista '{view_id}' no existe.")

    def _get_state(self, view_id: str) -> NavigationState:
        self._require_view(view_id)
        return self._views[view_id]

    @staticmethod
    def _normalize_path(path: str) -> Path:
        if not path or not str(path).strip():
            raise ValueError("La ruta no puede estar vacía.")
        return Path(path).expanduser().resolve()


__all__ = [
    "NavigationController",
    "NavigationState",
]