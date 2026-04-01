from PySide6.QtWidgets import QTreeView
from drag_drop_manager import DragDropManager, FileOperationsAdapter, build_default_drop_target_resolver

class MyFileOps(FileOperationsAdapter):
    def __init__(self, file_operations_module):
        self.ops = file_operations_module

    def copy_items(self, sources, destination_dir):
        self.ops.copy_items(list(sources), destination_dir)

    def move_items(self, sources, destination_dir):
        self.ops.move_items(list(sources), destination_dir)

    def link_items(self, sources, destination_dir):
        self.ops.create_shortcuts(list(sources), destination_dir)

    def same_filesystem(self, src, dst_dir):
        from pathlib import Path
        return Path(src).drive.lower() == Path(dst_dir).drive.lower()


def selected_paths_getter(view):
    # Implementa según tu modelo
    return view.property("selected_paths") or []

def current_dir_getter(view):
    return view.property("current_dir")

def panel_id_getter(view):
    return view.objectName()

manager = DragDropManager(
    file_operations=MyFileOps(file_operations),
    panel_id_getter=panel_id_getter,
    current_dir_getter=current_dir_getter,
    selected_paths_getter=selected_paths_getter,
    drop_target_resolver=build_default_drop_target_resolver(),
    confirm_external_move=False,
    enable_context_menu_on_ambiguous_drop=False,
)

manager.register_view(left_panel_view)
manager.register_view(right_panel_view)