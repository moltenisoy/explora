from __future__ import annotations

import ctypes
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QObject,
    QPoint,
    QPointF,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCursor,
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QGuiApplication,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QMenu,
    QMessageBox,
    QWidget,
)


# =============================================================================
# Windows native helpers
# =============================================================================

if os.name == "nt":
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    CF_HDROP = 15
    DROPEFFECT_NONE = 0
    DROPEFFECT_COPY = 1
    DROPEFFECT_MOVE = 2
    DROPEFFECT_LINK = 4

    MK_LBUTTON = 0x0001
    MK_RBUTTON = 0x0002
    MK_SHIFT = 0x0004
    MK_CONTROL = 0x0008
    MK_MBUTTON = 0x0010
    MK_ALT = 0x0020

    VK_ESCAPE = 0x1B

    class DROPFILES(ctypes.Structure):
        _fields_ = [
            ("pFiles", wintypes.DWORD),
            ("pt_x", wintypes.LONG),
            ("pt_y", wintypes.LONG),
            ("fNC", wintypes.BOOL),
            ("fWide", wintypes.BOOL),
        ]

    shell32.DragQueryFileW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPWSTR,
        wintypes.UINT,
    ]
    shell32.DragQueryFileW.restype = wintypes.UINT

    shell32.DragFinish.argtypes = [wintypes.HANDLE]
    shell32.DragFinish.restype = None

    user32.GetKeyState.argtypes = [ctypes.c_int]
    user32.GetKeyState.restype = ctypes.c_short

    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short

else:
    CF_HDROP = 15
    DROPEFFECT_NONE = 0
    DROPEFFECT_COPY = 1
    DROPEFFECT_MOVE = 2
    DROPEFFECT_LINK = 4


# =============================================================================
# Data contracts
# =============================================================================

@dataclass
class DragPayload:
    """
    Payload normalizado para operaciones drag & drop.
    """
    source_panel_id: Optional[str]
    source_paths: List[str]
    proposed_action: Qt.DropAction
    is_internal: bool
    is_external_windows: bool


@dataclass
class DropTargetContext:
    """
    Contexto resuelto del destino.
    target_panel_id: identificador lógico del panel destino.
    target_directory: carpeta actual del panel.
    target_index: opcional, índice/model item si el host quiere usarlo.
    """
    target_panel_id: Optional[str]
    target_directory: Optional[str]
    target_index: object = None


# =============================================================================
# Integration interfaces
# =============================================================================

class FileOperationsAdapter:
    """
    Adaptador mínimo para desacoplar DragDropManager del módulo real file_operations.

    Puedes reemplazar esta clase por tu integración directa si ya tienes un módulo
    con firmas concretas.
    """

    def copy_items(self, sources: Sequence[str], destination_dir: str) -> None:
        for src in sources:
            src_path = Path(src)
            dst = Path(destination_dir) / src_path.name
            if src_path.is_dir():
                shutil.copytree(src_path, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src_path, dst)

    def move_items(self, sources: Sequence[str], destination_dir: str) -> None:
        for src in sources:
            src_path = Path(src)
            dst = Path(destination_dir) / src_path.name
            shutil.move(str(src_path), str(dst))

    def link_items(self, sources: Sequence[str], destination_dir: str) -> None:
        raise NotImplementedError("link_items no implementado en este adaptador.")

    def same_filesystem(self, src: str, dst_dir: str) -> bool:
        try:
            return Path(src).drive.lower() == Path(dst_dir).drive.lower()
        except Exception:
            return False


# =============================================================================
# Main manager
# =============================================================================

class DragDropManager(QObject):
    """
    Gestor central de drag & drop real para Windows + PySide6.

    Casos soportados:
    - Drag interno entre paneles de la aplicación.
    - Drop desde Explorador de Windows / escritorio al panel.
    - Multiarchivo.
    - Copy / Move según modificadores estilo Windows.
    - Integración opcional con file_operations.

    El host debe proporcionar:
    - Un widget/view por panel.
    - Una forma de resolver:
        * selección actual del panel
        * directorio actual del panel
        * directorio/index destino a partir del drop
    """

    drag_started = Signal(object)          # DragPayload
    drag_finished = Signal(object, object) # DragPayload, Qt.DropAction
    drop_received = Signal(object, object) # DragPayload, DropTargetContext
    operation_failed = Signal(str)
    operation_succeeded = Signal(list, str, str)  # sources, destination, operation

    INTERNAL_MIME = "application/x-dragdropmanager-items"

    def __init__(
        self,
        parent: Optional[QObject] = None,
        *,
        file_operations: Optional[FileOperationsAdapter] = None,
        panel_id_getter: Optional[Callable[[QWidget], Optional[str]]] = None,
        current_dir_getter: Optional[Callable[[QWidget], Optional[str]]] = None,
        selected_paths_getter: Optional[Callable[[QWidget], Sequence[str]]] = None,
        drop_target_resolver: Optional[Callable[[QWidget, QDropEvent | QDragMoveEvent], DropTargetContext]] = None,
        confirm_external_move: bool = False,
        enable_context_menu_on_ambiguous_drop: bool = False,
    ) -> None:
        super().__init__(parent)
        self.file_operations = file_operations or FileOperationsAdapter()
        self.panel_id_getter = panel_id_getter
        self.current_dir_getter = current_dir_getter
        self.selected_paths_getter = selected_paths_getter
        self.drop_target_resolver = drop_target_resolver
        self.confirm_external_move = confirm_external_move
        self.enable_context_menu_on_ambiguous_drop = enable_context_menu_on_ambiguous_drop

        self._registered_views: List[QWidget] = []
        self._active_drag_payload: Optional[DragPayload] = None
        self._drag_start_pos: Optional[QPoint] = None
        self._drag_source_widget: Optional[QWidget] = None

    # -------------------------------------------------------------------------
    # Public registration API
    # -------------------------------------------------------------------------

    def register_view(self, view: QWidget) -> None:
        """
        Registra un widget de panel para drag & drop.

        Recomendado: usar sobre QTreeView / QListView / QTableView.
        """
        if view in self._registered_views:
            return

        view.setAcceptDrops(True)
        view.installEventFilter(self)

        if isinstance(view, QAbstractItemView):
            view.setDragEnabled(False)  # el manager inicia el drag manualmente
            view.setDropIndicatorShown(True)
            view.setDefaultDropAction(Qt.MoveAction)
            view.viewport().installEventFilter(self)
            view.viewport().setAcceptDrops(True)

        self._registered_views.append(view)

    def unregister_view(self, view: QWidget) -> None:
        if view not in self._registered_views:
            return

        view.removeEventFilter(self)
        if isinstance(view, QAbstractItemView):
            view.viewport().removeEventFilter(self)

        self._registered_views.remove(view)

    # -------------------------------------------------------------------------
    # Event filter
    # -------------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        host_view = self._resolve_host_view(watched)

        if host_view is None:
            return super().eventFilter(watched, event)

        et = event.type()

        if et == QEvent.MouseButtonPress:
            return self._handle_mouse_press(host_view, event) or super().eventFilter(watched, event)

        if et == QEvent.MouseMove:
            if self._handle_mouse_move(host_view, event):
                return True
            return super().eventFilter(watched, event)

        if et == QEvent.DragEnter:
            self._handle_drag_enter(host_view, event)
            return True

        if et == QEvent.DragMove:
            self._handle_drag_move(host_view, event)
            return True

        if et == QEvent.Drop:
            self._handle_drop(host_view, event)
            return True

        return super().eventFilter(watched, event)

    # -------------------------------------------------------------------------
    # Internal drag startup
    # -------------------------------------------------------------------------

    def _handle_mouse_press(self, view: QWidget, event: QEvent) -> bool:
        mouse_button = getattr(event, "button", lambda: None)()
        if mouse_button == Qt.LeftButton:
            pos = getattr(event, "pos", lambda: None)()
            self._drag_start_pos = pos
            self._drag_source_widget = view
        return False

    def _handle_mouse_move(self, view: QWidget, event: QEvent) -> bool:
        buttons = getattr(event, "buttons", lambda: Qt.NoButton)()
        if not (buttons & Qt.LeftButton):
            return False

        if self._drag_start_pos is None or self._drag_source_widget is None:
            return False

        pos = getattr(event, "pos", lambda: None)()
        if pos is None:
            return False

        if (pos - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return False

        if view is not self._drag_source_widget:
            return False

        self.start_drag(view)
        return True

    def start_drag(self, source_view: QWidget) -> None:
        selected_paths = list(self._get_selected_paths(source_view))
        if not selected_paths:
            return

        source_panel_id = self._get_panel_id(source_view)
        proposed_action = self._default_internal_action_for_selection(
            selected_paths,
            self._get_current_dir(source_view),
        )

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in selected_paths])

        encoded = self._serialize_internal_payload(
            source_panel_id=source_panel_id,
            paths=selected_paths,
            proposed_action=proposed_action,
        )
        mime.setData(self.INTERNAL_MIME, encoded)

        drag = QDrag(source_view)
        drag.setMimeData(mime)

        payload = DragPayload(
            source_panel_id=source_panel_id,
            source_paths=selected_paths,
            proposed_action=proposed_action,
            is_internal=True,
            is_external_windows=False,
        )
        self._active_drag_payload = payload
        self.drag_started.emit(payload)

        supported = Qt.CopyAction | Qt.MoveAction
        result = drag.exec(supported, proposed_action)

        self.drag_finished.emit(payload, result)
        self._active_drag_payload = None

    # -------------------------------------------------------------------------
    # Drag enter / move
    # -------------------------------------------------------------------------

    def _handle_drag_enter(self, target_view: QWidget, event: QDragEnterEvent) -> None:
        payload = self._extract_payload_from_mime(event.mimeData())
        if not payload:
            event.ignore()
            return

        if not self._can_accept_drop(target_view, payload):
            event.ignore()
            return

        action = self._resolve_drop_action(payload, target_view, event)
        event.setDropAction(action)
        event.accept()

    def _handle_drag_move(self, target_view: QWidget, event: QDragMoveEvent) -> None:
        payload = self._extract_payload_from_mime(event.mimeData())
        if not payload:
            event.ignore()
            return

        if not self._can_accept_drop(target_view, payload):
            event.ignore()
            return

        action = self._resolve_drop_action(payload, target_view, event)
        event.setDropAction(action)
        event.accept()

    # -------------------------------------------------------------------------
    # Drop handling
    # -------------------------------------------------------------------------

    def _handle_drop(self, target_view: QWidget, event: QDropEvent) -> None:
        payload = self._extract_payload_from_mime(event.mimeData())
        if not payload:
            event.ignore()
            return

        target_ctx = self._resolve_drop_target(target_view, event)
        if not target_ctx.target_directory:
            event.ignore()
            self.operation_failed.emit("No se pudo resolver el directorio destino del drop.")
            return

        if not self._can_accept_drop(target_view, payload):
            event.ignore()
            return

        action = self._resolve_drop_action(payload, target_view, event)

        if self.enable_context_menu_on_ambiguous_drop:
            chosen = self._maybe_show_drop_menu(target_view, payload, target_ctx, action)
            if chosen is None:
                event.ignore()
                return
            action = chosen

        try:
            self._execute_drop(payload, target_ctx, action)
        except Exception as exc:
            event.ignore()
            self.operation_failed.emit(str(exc))
            return

        event.setDropAction(action)
        event.accept()
        self.drop_received.emit(payload, target_ctx)

    # -------------------------------------------------------------------------
    # Payload extraction
    # -------------------------------------------------------------------------

    def _extract_payload_from_mime(self, mime: QMimeData) -> Optional[DragPayload]:
        if mime.hasFormat(self.INTERNAL_MIME):
            try:
                return self._deserialize_internal_payload(bytes(mime.data(self.INTERNAL_MIME)))
            except Exception:
                return None

        if mime.hasUrls():
            paths = []
            for url in mime.urls():
                if url.isLocalFile():
                    local = url.toLocalFile()
                    if local:
                        paths.append(local)

            if paths:
                return DragPayload(
                    source_panel_id=None,
                    source_paths=paths,
                    proposed_action=Qt.CopyAction,
                    is_internal=False,
                    is_external_windows=True,
                )

        return None

    # -------------------------------------------------------------------------
    # Decision logic
    # -------------------------------------------------------------------------

    def _can_accept_drop(self, target_view: QWidget, payload: DragPayload) -> bool:
        target_dir = self._get_current_dir(target_view)
        if not target_dir:
            return False

        for src in payload.source_paths:
            try:
                src_resolved = str(Path(src).resolve())
                dst_resolved = str(Path(target_dir).resolve())
                if src_resolved == dst_resolved:
                    return False
            except Exception:
                pass
        return True

    def _resolve_drop_action(
        self,
        payload: DragPayload,
        target_view: QWidget,
        event: QDropEvent | QDragMoveEvent | QDragEnterEvent,
    ) -> Qt.DropAction:
        modifiers = QApplication.keyboardModifiers()
        target_dir = self._get_current_dir(target_view)

        # Convención Windows:
        # - mismo volumen: Move
        # - distinto volumen: Copy
        # - Ctrl fuerza Copy
        # - Shift fuerza Move
        # - Alt puede sugerir Link
        if modifiers & Qt.ControlModifier:
            return Qt.CopyAction
        if modifiers & Qt.ShiftModifier:
            return Qt.MoveAction
        if modifiers & Qt.AltModifier:
            return Qt.LinkAction

        if target_dir and payload.source_paths:
            same_volume = all(
                self.file_operations.same_filesystem(src, target_dir)
                for src in payload.source_paths
            )
            return Qt.MoveAction if same_volume else Qt.CopyAction

        return payload.proposed_action or Qt.CopyAction

    def _default_internal_action_for_selection(
        self,
        selected_paths: Sequence[str],
        source_dir: Optional[str],
    ) -> Qt.DropAction:
        return Qt.MoveAction if source_dir else Qt.CopyAction

    # -------------------------------------------------------------------------
    # Execute operation
    # -------------------------------------------------------------------------

    def _execute_drop(
        self,
        payload: DragPayload,
        target_ctx: DropTargetContext,
        action: Qt.DropAction,
    ) -> None:
        destination = target_ctx.target_directory
        if not destination:
            raise ValueError("Destino inválido.")

        sources = list(payload.source_paths)
        op_name = self._drop_action_to_name(action)

        if action == Qt.CopyAction:
            self.file_operations.copy_items(sources, destination)

        elif action == Qt.MoveAction:
            if payload.is_external_windows and self.confirm_external_move:
                if not self._confirm_move_from_external(sources, destination):
                    raise RuntimeError("Operación cancelada por el usuario.")
            self.file_operations.move_items(sources, destination)

        elif action == Qt.LinkAction:
            self.file_operations.link_items(sources, destination)

        else:
            raise ValueError("Acción de drop no soportada.")

        self.operation_succeeded.emit(sources, destination, op_name)

    # -------------------------------------------------------------------------
    # Optional shell-style drop menu
    # -------------------------------------------------------------------------

    def _maybe_show_drop_menu(
        self,
        parent: QWidget,
        payload: DragPayload,
        target_ctx: DropTargetContext,
        default_action: Qt.DropAction,
    ) -> Optional[Qt.DropAction]:
        menu = QMenu(parent)

        act_copy = QAction("Copiar aquí", menu)
        act_move = QAction("Mover aquí", menu)
        act_link = QAction("Crear acceso directo aquí", menu)
        act_cancel = QAction("Cancelar", menu)

        menu.addAction(act_copy)
        menu.addAction(act_move)
        menu.addAction(act_link)
        menu.addSeparator()
        menu.addAction(act_cancel)

        if default_action == Qt.CopyAction:
            act_copy.setEnabled(True)
        elif default_action == Qt.MoveAction:
            act_move.setEnabled(True)
        elif default_action == Qt.LinkAction:
            act_link.setEnabled(True)

        chosen = menu.exec(QCursor.pos())
        if chosen is None or chosen == act_cancel:
            return None
        if chosen == act_copy:
            return Qt.CopyAction
        if chosen == act_move:
            return Qt.MoveAction
        if chosen == act_link:
            return Qt.LinkAction
        return None

    def _confirm_move_from_external(self, sources: Sequence[str], destination: str) -> bool:
        parent = self.parent() if isinstance(self.parent(), QWidget) else None
        text = (
            f"Se moverán {len(sources)} elemento(s) a:\n{destination}\n\n"
            "¿Deseas continuar?"
        )
        res = QMessageBox.question(
            parent,
            "Confirmar movimiento",
            text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return res == QMessageBox.Yes

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def _serialize_internal_payload(
        self,
        *,
        source_panel_id: Optional[str],
        paths: Sequence[str],
        proposed_action: Qt.DropAction,
    ) -> bytes:
        action_name = self._drop_action_to_name(proposed_action)
        lines = [
            f"panel={source_panel_id or ''}",
            f"action={action_name}",
        ]
        lines.extend(paths)
        return "\n".join(lines).encode("utf-8")

    def _deserialize_internal_payload(self, data: bytes) -> DragPayload:
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) < 2:
            raise ValueError("Payload DnD interno inválido.")

        panel_line = lines[0]
        action_line = lines[1]
        paths = [line for line in lines[2:] if line.strip()]

        source_panel_id = panel_line.partition("=")[2] or None
        action_name = action_line.partition("=")[2].strip().lower()
        action = self._name_to_drop_action(action_name)

        return DragPayload(
            source_panel_id=source_panel_id,
            source_paths=paths,
            proposed_action=action,
            is_internal=True,
            is_external_windows=False,
        )

    # -------------------------------------------------------------------------
    # Host callbacks
    # -------------------------------------------------------------------------

    def _resolve_drop_target(
        self,
        target_view: QWidget,
        event: QDropEvent | QDragMoveEvent,
    ) -> DropTargetContext:
        if self.drop_target_resolver:
            return self.drop_target_resolver(target_view, event)

        return DropTargetContext(
            target_panel_id=self._get_panel_id(target_view),
            target_directory=self._get_current_dir(target_view),
            target_index=None,
        )

    def _get_panel_id(self, view: QWidget) -> Optional[str]:
        if self.panel_id_getter:
            return self.panel_id_getter(view)
        return view.objectName() or None

    def _get_current_dir(self, view: QWidget) -> Optional[str]:
        if self.current_dir_getter:
            return self.current_dir_getter(view)

        value = view.property("current_dir")
        return str(value) if value else None

    def _get_selected_paths(self, view: QWidget) -> Sequence[str]:
        if self.selected_paths_getter:
            return self.selected_paths_getter(view)

        value = view.property("selected_paths")
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        return []

    def _resolve_host_view(self, watched: QObject) -> Optional[QWidget]:
        for view in self._registered_views:
            if watched is view:
                return view
            if isinstance(view, QAbstractItemView) and watched is view.viewport():
                return view
        return None

    # -------------------------------------------------------------------------
    # Utils
    # -------------------------------------------------------------------------

    @staticmethod
    def _drop_action_to_name(action: Qt.DropAction) -> str:
        if action == Qt.CopyAction:
            return "copy"
        if action == Qt.MoveAction:
            return "move"
        if action == Qt.LinkAction:
            return "link"
        return "none"

    @staticmethod
    def _name_to_drop_action(name: str) -> Qt.DropAction:
        name = name.lower()
        if name == "copy":
            return Qt.CopyAction
        if name == "move":
            return Qt.MoveAction
        if name == "link":
            return Qt.LinkAction
        return Qt.IgnoreAction


# =============================================================================
# Optional Windows-native helpers for future extension
# =============================================================================

class WindowsDropApi:
    """
    Helper de bajo nivel para extensiones nativas en Windows.

    Este archivo ya funciona con PySide6 usando QMimeData/QUrl para Explorer/Desktop,
    pero esta clase deja preparadas utilidades nativas reales para escenarios donde
    quieras inspeccionar HDROP o interoperar con estructuras Win32/Shell más avanzadas.
    """

    @staticmethod
    def extract_paths_from_hdrop(handle: int) -> List[str]:
        if os.name != "nt":
            return []

        hdrop = ctypes.c_void_p(handle)
        count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        results: List[str] = []

        for i in range(count):
            length = shell32.DragQueryFileW(hdrop, i, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(hdrop, i, buffer, length + 1)
            results.append(buffer.value)

        return results

    @staticmethod
    def is_escape_pressed() -> bool:
        if os.name != "nt":
            return False
        return bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)

    @staticmethod
    def current_windows_drop_effect_from_modifiers() -> int:
        if os.name != "nt":
            return DROPEFFECT_COPY

        ctrl = bool(user32.GetKeyState(0x11) & 0x8000)
        shift = bool(user32.GetKeyState(0x10) & 0x8000)
        alt = bool(user32.GetKeyState(0x12) & 0x8000)

        if alt:
            return DROPEFFECT_LINK
        if ctrl:
            return DROPEFFECT_COPY
        if shift:
            return DROPEFFECT_MOVE
        return DROPEFFECT_COPY


# =============================================================================
# Example integration helpers
# =============================================================================

def build_default_drop_target_resolver(
    current_dir_from_index: Optional[Callable[[QWidget, object], Optional[str]]] = None
) -> Callable[[QWidget, QDropEvent | QDragMoveEvent], DropTargetContext]:
    """
    Crea un resolver simple para vistas tipo item-view.

    Si el índice bajo el cursor representa una carpeta, puedes devolver esa carpeta
    como destino. Si no, cae al directorio actual del panel.
    """

    def resolver(view: QWidget, event: QDropEvent | QDragMoveEvent) -> DropTargetContext:
        panel_id = view.objectName() or None
        target_dir = view.property("current_dir")
        target_index = None

        if isinstance(view, QAbstractItemView):
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            index = view.indexAt(pos)
            if index.isValid():
                target_index = index
                if current_dir_from_index:
                    maybe_dir = current_dir_from_index(view, index)
                    if maybe_dir:
                        target_dir = maybe_dir

        return DropTargetContext(
            target_panel_id=panel_id,
            target_directory=str(target_dir) if target_dir else None,
            target_index=target_index,
        )

    return resolver