from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Protocol


class NavigationControllerProtocol(Protocol):
    """
    Contrato mínimo esperado para el controlador de navegación.

    La implementación real puede tener más métodos; TwinModeManager
    solo depende de esta superficie mínima para mantener bajo acoplamiento.
    """

    def create_embedded_instance(self, container_id: str, panel_id: str) -> Any:
        """Crea una instancia embebida de navegador/vista dentro de un contenedor."""

    def navigate(self, instance: Any, target: str) -> None:
        """Navega una instancia hacia un destino/ruta/url."""

    def get_current_location(self, instance: Any) -> str:
        """Retorna la ubicación actual de la instancia."""

    def set_active_instance(self, instance: Any) -> None:
        """Marca una instancia como activa en la UI/controlador."""


class DragDropManagerProtocol(Protocol):
    """
    Contrato mínimo esperado para el manager de drag & drop.
    """

    def register_panel(self, panel_id: str, panel_ref: Any) -> None:
        """Registra un panel como origen/destino de DnD."""

    def connect_panels(self, source_panel_id: str, target_panel_id: str) -> None:
        """Habilita DnD entre dos paneles."""

    def disconnect_panels(self, source_panel_id: str, target_panel_id: str) -> None:
        """Deshabilita DnD entre dos paneles."""

    def unregister_panel(self, panel_id: str) -> None:
        """Quita un panel del sistema de DnD."""


@dataclass
class PanelState:
    """
    Estado aislado de un panel.

    Diseñado para que a futuro pueda extenderse con tabs, historial,
    selección, filtros, metadatos visuales, etc.
    """

    panel_id: str
    container_id: str
    instance: Any = None
    active: bool = False
    current_location: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TwinModeConfig:
    """
    Configuración de comportamiento del modo gemelo.
    """

    sync_enabled: bool = False
    mirror_navigation_on_activate: bool = False
    allow_drag_drop_between_panels: bool = True


class TwinModeError(Exception):
    """Error base del modo gemelo."""


class PanelNotFoundError(TwinModeError):
    """Se intenta operar sobre un panel inexistente."""


class TwinModeAlreadyEnabledError(TwinModeError):
    """Se intenta habilitar el modo gemelo cuando ya está activo."""


class TwinModeNotEnabledError(TwinModeError):
    """Se intenta operar sobre el modo gemelo cuando no está activo."""


class TwinModeManager:
    """
    Gestiona el modo gemelo (doble panel independiente).

    Responsabilidades:
    - Crear y destruir el segundo panel embebido
    - Mantener estado independiente por panel
    - Delegar navegación al navigation_controller
    - Delegar drag & drop al drag_drop_manager
    - Ofrecer sincronización opcional entre paneles

    Principios:
    - Alta cohesión: toda la lógica de modo gemelo vive aquí
    - Bajo acoplamiento: depende de protocolos mínimos
    - Extensible: PanelState deja preparado el terreno para multi-tab
    """

    PRIMARY_PANEL_ID = "primary"
    SECONDARY_PANEL_ID = "secondary"

    def __init__(
        self,
        navigation_controller: NavigationControllerProtocol,
        drag_drop_manager: DragDropManagerProtocol,
        *,
        config: Optional[TwinModeConfig] = None,
        on_layout_changed: Optional[Callable[[bool], None]] = None,
        on_panel_created: Optional[Callable[[PanelState], None]] = None,
        on_panel_destroyed: Optional[Callable[[str], None]] = None,
        on_sync_changed: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self._navigation_controller = navigation_controller
        self._drag_drop_manager = drag_drop_manager
        self._config = config or TwinModeConfig()

        self._on_layout_changed = on_layout_changed
        self._on_panel_created = on_panel_created
        self._on_panel_destroyed = on_panel_destroyed
        self._on_sync_changed = on_sync_changed

        self._enabled = False
        self._panels: Dict[str, PanelState] = {}
        self._active_panel_id: Optional[str] = None

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def initialize_primary_panel(
        self,
        *,
        container_id: str,
        instance: Any,
        current_location: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PanelState:
        """
        Registra el panel principal existente.

        Debe llamarse una vez cuando la app crea el panel principal.
        """
        state = PanelState(
            panel_id=self.PRIMARY_PANEL_ID,
            container_id=container_id,
            instance=instance,
            active=True,
            current_location=current_location,
            metadata=metadata or {},
        )
        self._panels[self.PRIMARY_PANEL_ID] = state
        self._active_panel_id = self.PRIMARY_PANEL_ID

        self._drag_drop_manager.register_panel(state.panel_id, state.instance)
        self._navigation_controller.set_active_instance(state.instance)

        return state

    def enable(
        self,
        *,
        secondary_container_id: str,
        initial_target: Optional[str] = None,
        clone_from_primary: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PanelState:
        """
        Habilita modo gemelo creando la segunda instancia embebida.
        """
        if self._enabled:
            raise TwinModeAlreadyEnabledError("Twin mode ya está habilitado.")

        primary = self.get_panel(self.PRIMARY_PANEL_ID)

        secondary_instance = self._navigation_controller.create_embedded_instance(
            secondary_container_id,
            self.SECONDARY_PANEL_ID,
        )

        location = initial_target
        if location is None and clone_from_primary:
            location = primary.current_location or self._safe_get_location(primary.instance)

        secondary = PanelState(
            panel_id=self.SECONDARY_PANEL_ID,
            container_id=secondary_container_id,
            instance=secondary_instance,
            active=False,
            current_location=None,
            metadata=metadata or {},
        )

        self._panels[self.SECONDARY_PANEL_ID] = secondary
        self._drag_drop_manager.register_panel(secondary.panel_id, secondary.instance)

        if self._config.allow_drag_drop_between_panels:
            self._connect_drag_drop()

        if location:
            self.navigate(self.SECONDARY_PANEL_ID, location)

        self._enabled = True

        if self._on_panel_created:
            self._on_panel_created(secondary)
        if self._on_layout_changed:
            self._on_layout_changed(True)

        return secondary

    def disable(self) -> None:
        """
        Deshabilita modo gemelo y elimina el panel secundario del manager.

        Nota:
        La destrucción visual real del widget/contenedor puede quedar
        a cargo de la capa UI. Este manager se concentra en coordinar estado
        e integraciones.
        """
        if not self._enabled:
            raise TwinModeNotEnabledError("Twin mode no está habilitado.")

        secondary = self.get_panel(self.SECONDARY_PANEL_ID)

        if self._config.allow_drag_drop_between_panels:
            self._disconnect_drag_drop()

        self._drag_drop_manager.unregister_panel(secondary.panel_id)
        del self._panels[self.SECONDARY_PANEL_ID]

        self._enabled = False

        if self._active_panel_id == self.SECONDARY_PANEL_ID:
            self.set_active_panel(self.PRIMARY_PANEL_ID)

        if self._on_panel_destroyed:
            self._on_panel_destroyed(self.SECONDARY_PANEL_ID)
        if self._on_layout_changed:
            self._on_layout_changed(False)

    # -------------------------------------------------------------------------
    # Panel access
    # -------------------------------------------------------------------------

    def is_enabled(self) -> bool:
        return self._enabled

    def has_panel(self, panel_id: str) -> bool:
        return panel_id in self._panels

    def get_panel(self, panel_id: str) -> PanelState:
        try:
            return self._panels[panel_id]
        except KeyError as exc:
            raise PanelNotFoundError(f"Panel no encontrado: {panel_id}") from exc

    def get_primary_panel(self) -> PanelState:
        return self.get_panel(self.PRIMARY_PANEL_ID)

    def get_secondary_panel(self) -> PanelState:
        return self.get_panel(self.SECONDARY_PANEL_ID)

    def get_active_panel(self) -> PanelState:
        if self._active_panel_id is None:
            raise PanelNotFoundError("No hay panel activo.")
        return self.get_panel(self._active_panel_id)

    def list_panels(self) -> list[PanelState]:
        """
        Retorna los paneles en orden lógico.
        """
        ordered_ids = [self.PRIMARY_PANEL_ID, self.SECONDARY_PANEL_ID]
        return [self._panels[panel_id] for panel_id in ordered_ids if panel_id in self._panels]

    # -------------------------------------------------------------------------
    # Navigation
    # -------------------------------------------------------------------------

    def navigate(self, panel_id: str, target: str) -> None:
        """
        Navega de forma independiente el panel indicado.

        Si la sincronización está habilitada, replica la navegación
        en el panel opuesto.
        """
        panel = self.get_panel(panel_id)
        self._navigation_controller.navigate(panel.instance, target)
        panel.current_location = target

        if self._config.sync_enabled:
            other = self.get_opposite_panel(panel_id)
            if other is not None:
                self._navigation_controller.navigate(other.instance, target)
                other.current_location = target

    def refresh_locations(self) -> None:
        """
        Re-sincroniza el estado local leyendo la ubicación actual
        de cada instancia desde navigation_controller.
        """
        for panel in self._panels.values():
            panel.current_location = self._safe_get_location(panel.instance)

    def sync_from(self, source_panel_id: str) -> None:
        """
        Fuerza sincronización manual desde un panel origen al opuesto.
        """
        source = self.get_panel(source_panel_id)
        target = self.get_opposite_panel(source_panel_id)

        if target is None:
            return

        location = source.current_location or self._safe_get_location(source.instance)
        if not location:
            return

        self._navigation_controller.navigate(target.instance, location)
        target.current_location = location

    def get_opposite_panel(self, panel_id: str) -> Optional[PanelState]:
        if panel_id == self.PRIMARY_PANEL_ID:
            return self._panels.get(self.SECONDARY_PANEL_ID)
        if panel_id == self.SECONDARY_PANEL_ID:
            return self._panels.get(self.PRIMARY_PANEL_ID)
        raise PanelNotFoundError(f"Panel no reconocido: {panel_id}")

    # -------------------------------------------------------------------------
    # Active panel / focus
    # -------------------------------------------------------------------------

    def set_active_panel(self, panel_id: str) -> None:
        """
        Marca un panel como activo y notifica al navigation_controller.
        """
        panel = self.get_panel(panel_id)

        for state in self._panels.values():
            state.active = False

        panel.active = True
        self._active_panel_id = panel_id
        self._navigation_controller.set_active_instance(panel.instance)

        if self._config.sync_enabled and self._config.mirror_navigation_on_activate:
            self.sync_from(panel_id)

    # -------------------------------------------------------------------------
    # Sync
    # -------------------------------------------------------------------------

    def set_sync_enabled(self, enabled: bool) -> None:
        self._config.sync_enabled = enabled
        if self._on_sync_changed:
            self._on_sync_changed(enabled)

    def toggle_sync(self) -> bool:
        self._config.sync_enabled = not self._config.sync_enabled
        if self._on_sync_changed:
            self._on_sync_changed(self._config.sync_enabled)
        return self._config.sync_enabled

    def is_sync_enabled(self) -> bool:
        return self._config.sync_enabled

    # -------------------------------------------------------------------------
    # Drag & drop integration
    # -------------------------------------------------------------------------

    def reconnect_drag_drop(self) -> None:
        """
        Reaplica la conexión DnD entre paneles.

        Útil si drag_drop_manager reinicia internamente o si la UI
        reconstruye referencias visuales.
        """
        if not self._enabled or not self._config.allow_drag_drop_between_panels:
            return

        self._disconnect_drag_drop(silent=True)
        self._connect_drag_drop()

    # -------------------------------------------------------------------------
    # Extensibility helpers
    # -------------------------------------------------------------------------

    def export_workspace_state(self) -> Dict[str, Any]:
        """
        Exporta un snapshot serializable del estado actual.

        Preparado para persistencia futura, restauración de sesión,
        layouts complejos o soporte multi-tab.
        """
        return {
            "twin_mode_enabled": self._enabled,
            "sync_enabled": self._config.sync_enabled,
            "active_panel_id": self._active_panel_id,
            "panels": {
                panel_id: {
                    "container_id": panel.container_id,
                    "active": panel.active,
                    "current_location": panel.current_location,
                    "metadata": dict(panel.metadata),
                }
                for panel_id, panel in self._panels.items()
            },
        }

    def import_workspace_state(self, state: Dict[str, Any]) -> None:
        """
        Restaura parcialmente flags y metadata de estado.

        No recrea instancias UI por sí mismo; esa responsabilidad sigue
        perteneciendo a la capa de composición de la aplicación.
        """
        self._config.sync_enabled = bool(state.get("sync_enabled", self._config.sync_enabled))
        self._active_panel_id = state.get("active_panel_id", self._active_panel_id)

        panel_states = state.get("panels", {})
        for panel_id, panel_data in panel_states.items():
            if panel_id not in self._panels:
                continue

            panel = self._panels[panel_id]
            panel.current_location = panel_data.get("current_location", panel.current_location)
            panel.active = bool(panel_data.get("active", panel.active))
            panel.metadata.update(panel_data.get("metadata", {}))

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _connect_drag_drop(self) -> None:
        primary = self.get_panel(self.PRIMARY_PANEL_ID)
        secondary = self.get_panel(self.SECONDARY_PANEL_ID)

        self._drag_drop_manager.connect_panels(primary.panel_id, secondary.panel_id)
        self._drag_drop_manager.connect_panels(secondary.panel_id, primary.panel_id)

    def _disconnect_drag_drop(self, silent: bool = False) -> None:
        try:
            self._drag_drop_manager.disconnect_panels(
                self.PRIMARY_PANEL_ID,
                self.SECONDARY_PANEL_ID,
            )
            self._drag_drop_manager.disconnect_panels(
                self.SECONDARY_PANEL_ID,
                self.PRIMARY_PANEL_ID,
            )
        except Exception:
            if not silent:
                raise

    def _safe_get_location(self, instance: Any) -> Optional[str]:
        try:
            return self._navigation_controller.get_current_location(instance)
        except Exception:
            return None