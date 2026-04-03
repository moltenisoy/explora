from __future__ import annotations

from pathlib import Path

from drag_drop_manager import DragDropManager
from navigation_controller import NavigationController
from twin_mode import TwinModeManager


def build_example_twin_mode(main_embedded_view) -> TwinModeManager:
    navigation_controller = NavigationController()
    drag_drop_manager = DragDropManager()
    twin_mode = TwinModeManager(
        navigation_controller=navigation_controller,
        drag_drop_manager=drag_drop_manager,
    )

    twin_mode.initialize_primary_panel(
        container_id="left-pane",
        instance=main_embedded_view,
        current_location=str(Path.home()),
    )

    twin_mode.enable(
        secondary_container_id="right-pane",
        clone_from_primary=True,
    )

    twin_mode.set_sync_enabled(True)
    twin_mode.sync_from("primary")
    return twin_mode
