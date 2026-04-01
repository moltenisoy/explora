twin_mode = TwinModeManager(
    navigation_controller=navigation_controller,
    drag_drop_manager=drag_drop_manager,
)

twin_mode.initialize_primary_panel(
    container_id="left-pane",
    instance=main_embedded_view,
    current_location="/home",
)

twin_mode.enable(
    secondary_container_id="right-pane",
    clone_from_primary=True,
)

twin_mode.navigate("secondary", "/documents")
twin_mode.set_sync_enabled(True)
twin_mode.sync_from("primary")