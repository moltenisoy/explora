from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class ViewMode(str, Enum):
    SINGLE = "single"
    SPLIT = "split"


@dataclass(slots=True)
class StartItem:
    key: str
    title: str
    subtitle: str = ""


class NavigationBar(QWidget):
    back_requested = Signal()
    forward_requested = Signal()
    up_requested = Signal()
    path_submitted = Signal(str)

    def __init__(self, *, show_path: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._show_path = show_path
        self._build_ui()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.back_button = QToolButton(self)
        self.back_button.setText("←")
        self.back_button.setToolTip("Atrás")
        self.back_button.clicked.connect(self.back_requested.emit)

        self.forward_button = QToolButton(self)
        self.forward_button.setText("→")
        self.forward_button.setToolTip("Adelante")
        self.forward_button.clicked.connect(self.forward_requested.emit)

        self.up_button = QToolButton(self)
        self.up_button.setText("↑")
        self.up_button.setToolTip("Subir nivel")
        self.up_button.clicked.connect(self.up_requested.emit)

        root.addWidget(self.back_button)
        root.addWidget(self.forward_button)
        root.addWidget(self.up_button)

        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText("Ruta completa")
        self.path_edit.returnPressed.connect(self._emit_path)

        if self._show_path:
            self.path_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            root.addWidget(self.path_edit)

    def _emit_path(self) -> None:
        self.path_submitted.emit(self.path_edit.text().strip())

    def set_path(self, path: str) -> None:
        self.path_edit.setText(path)

    def set_navigation_enabled(self, *, back: bool, forward: bool, up: bool) -> None:
        self.back_button.setEnabled(back)
        self.forward_button.setEnabled(forward)
        self.up_button.setEnabled(up)


class FavoritePanel(QWidget):
    favorite_activated = Signal(str)
    favorite_add_requested = Signal()
    favorite_remove_requested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Favoritos", self)
        title.setObjectName("favoritesTitle")

        self.add_button = QToolButton(self)
        self.add_button.setText("+")
        self.add_button.setToolTip("Agregar favorito")
        self.add_button.clicked.connect(self.favorite_add_requested.emit)

        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.add_button)

        self.list_widget = QListWidget(self)
        self.list_widget.itemDoubleClicked.connect(self._on_item_activated)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setObjectName("favoritesList")

        root.addLayout(header)
        root.addWidget(self.list_widget)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        self.favorite_activated.emit(item.data(Qt.ItemDataRole.UserRole) or item.text())

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self.list_widget.clear()
        for label, path in items:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.list_widget.addItem(item)

    def current_favorite_path(self) -> str:
        item = self.list_widget.currentItem()
        if not item:
            return ""
        return item.data(Qt.ItemDataRole.UserRole) or item.text()


class FileViewPlaceholder(QFrame):
    item_activated = Signal(str)
    selection_changed = Signal()
    context_menu_requested = Signal(object)
    drop_received = Signal(object)

    def __init__(self, title: str = "Vista de archivos", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._title = title
        self._build_ui()
        self.setAcceptDrops(True)

    def _build_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("fileView")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.title_label = QLabel(self._title, self)
        self.title_label.setObjectName("fileViewTitle")

        self.info_label = QLabel(
            "Área central dinámica preparada para integrar una vista real de archivos.",
            self,
        )
        self.info_label.setWordWrap(True)
        self.info_label.setObjectName("fileViewInfo")

        self.content_host = QFrame(self)
        self.content_host.setFrameShape(QFrame.Shape.StyledPanel)
        self.content_host.setObjectName("fileViewContentHost")
        self.content_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        content_layout = QVBoxLayout(self.content_host)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(8)

        self.empty_state = QLabel("Sin contenido conectado", self.content_host)
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setObjectName("fileViewEmptyState")

        content_layout.addStretch(1)
        content_layout.addWidget(self.empty_state)
        content_layout.addStretch(1)

        root.addWidget(self.title_label)
        root.addWidget(self.info_label)
        root.addWidget(self.content_host, 1)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self.drop_received.emit(event)
        event.acceptProposedAction()


class StartScreen(QWidget):
    trash_requested = Signal()
    control_panel_requested = Signal()
    active_unit_requested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Inicio", self)
        title.setObjectName("startScreenTitle")

        subtitle = QLabel(
            "Accesos rápidos iniciales del explorador. "
            "La lógica de navegación real debe conectarse externamente.",
            self,
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("startScreenSubtitle")

        quick_access = QHBoxLayout()
        quick_access.setSpacing(12)

        self.trash_button = self._create_card_button("Papelera", "Acceso a papelera")
        self.trash_button.clicked.connect(self.trash_requested.emit)

        self.control_panel_button = self._create_card_button("Panel de control", "Configuración del sistema")
        self.control_panel_button.clicked.connect(self.control_panel_requested.emit)

        quick_access.addWidget(self.trash_button)
        quick_access.addWidget(self.control_panel_button)
        quick_access.addStretch(1)

        units_label = QLabel("Unidades activas", self)
        units_label.setObjectName("startScreenSectionTitle")

        self.units_list = QListWidget(self)
        self.units_list.setObjectName("activeUnitsList")
        self.units_list.itemDoubleClicked.connect(self._on_unit_activated)

        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(quick_access)
        root.addWidget(units_label)
        root.addWidget(self.units_list, 1)

    def _create_card_button(self, title: str, subtitle: str) -> QPushButton:
        button = QPushButton(f"{title}\n{subtitle}", self)
        button.setMinimumHeight(72)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setObjectName("startCardButton")
        return button

    def _on_unit_activated(self, item: QListWidgetItem) -> None:
        self.active_unit_requested.emit(item.data(Qt.ItemDataRole.UserRole) or item.text())

    def set_active_units(self, units: list[tuple[str, str]]) -> None:
        self.units_list.clear()
        for label, path in units:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.units_list.addItem(item)


class ExplorerPane(QWidget):
    back_requested = Signal()
    forward_requested = Signal()
    up_requested = Signal()
    path_submitted = Signal(str)

    item_activated = Signal(str)
    selection_changed = Signal()
    context_menu_requested = Signal(object)
    drop_received = Signal(object)

    trash_requested = Signal()
    control_panel_requested = Signal()
    active_unit_requested = Signal(str)

    animation_hook_requested = Signal(str)

    def __init__(self, pane_name: str = "Panel", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.pane_name = pane_name
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.top_bar = NavigationBar(show_path=True, parent=self)
        self.bottom_bar = NavigationBar(show_path=False, parent=self)

        self.stack = QStackedWidget(self)
        self.start_screen = StartScreen(self)
        self.file_view = FileViewPlaceholder(title=f"Vista de archivos · {self.pane_name}", parent=self)

        self.stack.addWidget(self.start_screen)
        self.stack.addWidget(self.file_view)

        root.addWidget(self.top_bar)
        root.addWidget(self.stack, 1)
        root.addWidget(self.bottom_bar)

        self.top_bar.back_requested.connect(self.back_requested.emit)
        self.top_bar.forward_requested.connect(self.forward_requested.emit)
        self.top_bar.up_requested.connect(self.up_requested.emit)
        self.top_bar.path_submitted.connect(self.path_submitted.emit)

        self.bottom_bar.back_requested.connect(self.back_requested.emit)
        self.bottom_bar.forward_requested.connect(self.forward_requested.emit)
        self.bottom_bar.up_requested.connect(self.up_requested.emit)

        self.file_view.item_activated.connect(self.item_activated.emit)
        self.file_view.selection_changed.connect(self.selection_changed.emit)
        self.file_view.context_menu_requested.connect(self.context_menu_requested.emit)
        self.file_view.drop_received.connect(self.drop_received.emit)

        self.start_screen.trash_requested.connect(self.trash_requested.emit)
        self.start_screen.control_panel_requested.connect(self.control_panel_requested.emit)
        self.start_screen.active_unit_requested.connect(self.active_unit_requested.emit)

    def show_start_screen(self) -> None:
        self.stack.setCurrentWidget(self.start_screen)
        self.animation_hook_requested.emit("show_start_screen")

    def show_file_view(self) -> None:
        self.stack.setCurrentWidget(self.file_view)
        self.animation_hook_requested.emit("show_file_view")

    def set_path(self, path: str) -> None:
        self.top_bar.set_path(path)

    def set_navigation_enabled(self, *, back: bool, forward: bool, up: bool) -> None:
        self.top_bar.set_navigation_enabled(back=back, forward=forward, up=up)
        self.bottom_bar.set_navigation_enabled(back=back, forward=forward, up=up)

    def set_active_units(self, units: list[tuple[str, str]]) -> None:
        self.start_screen.set_active_units(units)


class MainExplorerUI(QMainWindow):
    back_requested = Signal(int)
    forward_requested = Signal(int)
    up_requested = Signal(int)
    path_submitted = Signal(int, str)

    favorite_activated = Signal(str)
    favorite_add_requested = Signal()
    favorite_remove_requested = Signal(str)

    item_activated = Signal(int, str)
    selection_changed = Signal(int)
    context_menu_requested = Signal(int, object)
    drop_received = Signal(int, object)

    trash_requested = Signal(int)
    control_panel_requested = Signal(int)
    active_unit_requested = Signal(int, str)

    split_mode_changed = Signal(bool)
    animation_hook_requested = Signal(str, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._view_mode = ViewMode.SINGLE
        self._build_ui()
        self._build_actions()
        self._connect_signals()
        self._apply_default_state()

    def _build_ui(self) -> None:
        self.setWindowTitle("Explorador")
        self.resize(1280, 800)
        self.setMinimumSize(QSize(960, 640))
        self.setAcceptDrops(True)

        central = QWidget(self)
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_splitter.setChildrenCollapsible(False)

        self.favorites_panel = FavoritePanel(self)

        self.workspace = QSplitter(Qt.Orientation.Horizontal, self)
        self.workspace.setChildrenCollapsible(False)
        self.workspace.setHandleWidth(6)

        self.left_pane = ExplorerPane("Izquierdo", self)
        self.right_pane = ExplorerPane("Derecho", self)

        self.workspace.addWidget(self.left_pane)
        self.workspace.addWidget(self.right_pane)
        self.workspace.setStretchFactor(0, 1)
        self.workspace.setStretchFactor(1, 1)

        self.main_splitter.addWidget(self.favorites_panel)
        self.main_splitter.addWidget(self.workspace)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([240, 1040])

        root.addWidget(self.main_splitter)

    def _build_actions(self) -> None:
        self.toggle_split_action = QAction("Modo gemelo", self)
        self.toggle_split_action.setCheckable(True)
        self.toggle_split_action.setShortcut("F6")

        self.show_favorites_action = QAction("Mostrar favoritos", self)
        self.show_favorites_action.setCheckable(True)
        self.show_favorites_action.setChecked(True)

    def _connect_signals(self) -> None:
        self.toggle_split_action.toggled.connect(self.set_split_mode)
        self.show_favorites_action.toggled.connect(self.favorites_panel.setVisible)

        self.favorites_panel.favorite_activated.connect(self.favorite_activated.emit)
        self.favorites_panel.favorite_add_requested.connect(self.favorite_add_requested.emit)

        self.left_pane.back_requested.connect(lambda: self.back_requested.emit(0))
        self.left_pane.forward_requested.connect(lambda: self.forward_requested.emit(0))
        self.left_pane.up_requested.connect(lambda: self.up_requested.emit(0))
        self.left_pane.path_submitted.connect(lambda path: self.path_submitted.emit(0, path))
        self.left_pane.item_activated.connect(lambda item: self.item_activated.emit(0, item))
        self.left_pane.selection_changed.connect(lambda: self.selection_changed.emit(0))
        self.left_pane.context_menu_requested.connect(lambda pos: self.context_menu_requested.emit(0, pos))
        self.left_pane.drop_received.connect(lambda event: self.drop_received.emit(0, event))
        self.left_pane.trash_requested.connect(lambda: self.trash_requested.emit(0))
        self.left_pane.control_panel_requested.connect(lambda: self.control_panel_requested.emit(0))
        self.left_pane.active_unit_requested.connect(lambda path: self.active_unit_requested.emit(0, path))
        self.left_pane.animation_hook_requested.connect(
            lambda key: self.animation_hook_requested.emit(key, 0)
        )

        self.right_pane.back_requested.connect(lambda: self.back_requested.emit(1))
        self.right_pane.forward_requested.connect(lambda: self.forward_requested.emit(1))
        self.right_pane.up_requested.connect(lambda: self.up_requested.emit(1))
        self.right_pane.path_submitted.connect(lambda path: self.path_submitted.emit(1, path))
        self.right_pane.item_activated.connect(lambda item: self.item_activated.emit(1, item))
        self.right_pane.selection_changed.connect(lambda: self.selection_changed.emit(1))
        self.right_pane.context_menu_requested.connect(lambda pos: self.context_menu_requested.emit(1, pos))
        self.right_pane.drop_received.connect(lambda event: self.drop_received.emit(1, event))
        self.right_pane.trash_requested.connect(lambda: self.trash_requested.emit(1))
        self.right_pane.control_panel_requested.connect(lambda: self.control_panel_requested.emit(1))
        self.right_pane.active_unit_requested.connect(lambda path: self.active_unit_requested.emit(1, path))
        self.right_pane.animation_hook_requested.connect(
            lambda key: self.animation_hook_requested.emit(key, 1)
        )

    def _apply_default_state(self) -> None:
        self.left_pane.show_start_screen()
        self.right_pane.show_start_screen()
        self.set_split_mode(False)

    def set_split_mode(self, enabled: bool) -> None:
        self._view_mode = ViewMode.SPLIT if enabled else ViewMode.SINGLE
        self.right_pane.setVisible(enabled)
        self.toggle_split_action.setChecked(enabled)
        self.split_mode_changed.emit(enabled)

    def is_split_mode(self) -> bool:
        return self._view_mode == ViewMode.SPLIT

    def set_favorites(self, items: list[tuple[str, str]]) -> None:
        self.favorites_panel.set_items(items)

    def set_active_units(self, units: list[tuple[str, str]], pane_index: Optional[int] = None) -> None:
        if pane_index is None:
            self.left_pane.set_active_units(units)
            self.right_pane.set_active_units(units)
            return

        self._pane(pane_index).set_active_units(units)

    def set_path(self, pane_index: int, path: str) -> None:
        self._pane(pane_index).set_path(path)

    def set_navigation_enabled(self, pane_index: int, *, back: bool, forward: bool, up: bool) -> None:
        self._pane(pane_index).set_navigation_enabled(back=back, forward=forward, up=up)

    def show_start_screen(self, pane_index: int) -> None:
        self._pane(pane_index).show_start_screen()

    def show_file_view(self, pane_index: int) -> None:
        self._pane(pane_index).show_file_view()

    def request_favorite_removal(self) -> None:
        path = self.favorites_panel.current_favorite_path()
        if path:
            self.favorite_remove_requested.emit(path)

    def _pane(self, index: int) -> ExplorerPane:
        if index == 0:
            return self.left_pane
        if index == 1:
            return self.right_pane
        raise IndexError(f"Pane index inválido: {index}")


if __name__ == "__main__":
    app = QApplication([])

    window = MainExplorerUI()
    window.set_favorites(
        [
            ("Inicio", "/home/user"),
            ("Documentos", "/home/user/Documents"),
            ("Descargas", "/home/user/Downloads"),
        ]
    )
    window.set_active_units(
        [
            ("Disco local (C:)", "C:/"),
            ("Datos (D:)", "D:/"),
            ("USB", "E:/"),
        ]
    )
    window.set_path(0, "C:/Users/demo")
    window.set_navigation_enabled(0, back=True, forward=False, up=True)
    window.show()

    app.exec()