import os
import sys
import stat
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any

from PySide6.QtWidgets import QMenu, QApplication, QInputDialog, QMessageBox
from PySide6.QtGui import QCursor


class ContextMenuManager:

    def __init__(self, parent, file_operations=None, clipboard_manager=None):
        self.parent = parent
        self.file_operations = file_operations
        self.clipboard_manager = clipboard_manager

        self.menu = QMenu(parent)
        self.current_selection: List[str] = []

        self.actions: Dict[str, Dict[str, Any]] = {}
        self._register_default_actions()

    def _register_action(
        self,
        action_id: str,
        label: str,
        callback: Callable[[], None],
        enabled_when: Optional[Callable[[List[str]], bool]] = None,
    ) -> None:
        self.actions[action_id] = {
            "label": label,
            "callback": callback,
            "enabled_when": enabled_when or (lambda selection: True),
        }

    def _register_default_actions(self) -> None:
        self._register_action("copy", "Copiar", self.copy, self._has_selection)
        self._register_action("cut", "Cortar", self.cut, self._has_selection)
        self._register_action("paste", "Pegar", self.paste, self._can_paste)
        self._register_action("delete_permanently", "Eliminar definitivo", self.delete_permanently, self._has_selection)
        self._register_action("create_shortcut", "Crear acceso directo", self.create_shortcut, self._has_single_selection)
        self._register_action("copy_content", "Copiar contenido al portapapeles", self.copy_content_to_clipboard, self._has_single_file_selection)
        self._register_action("rename", "Renombrar", self.rename, self._has_single_selection)
        self._register_action("properties", "Propiedades", self.show_properties, self._has_selection)
        self._register_action("take_ownership", "Tomar posesión", self.take_ownership, self._has_selection)

    def show(self, event, selection: List[str]) -> None:
        self.current_selection = selection or []
        self._rebuild_menu()
        self.menu.exec(QCursor.pos())

    def _rebuild_menu(self) -> None:
        self.menu.clear()

        ordered_actions = [
            "copy",
            "cut",
            "paste",
            None,
            "delete_permanently",
            "create_shortcut",
            "copy_content",
            "rename",
            None,
            "properties",
            "take_ownership",
        ]

        for action_id in ordered_actions:
            if action_id is None:
                self.menu.addSeparator()
                continue

            action_data = self.actions[action_id]
            enabled = action_data["enabled_when"](self.current_selection)

            action = self.menu.addAction(action_data["label"])
            action.setEnabled(enabled)
            action.triggered.connect(action_data["callback"])

    def add_action(
        self,
        action_id: str,
        label: str,
        callback: Callable[[], None],
        enabled_when: Optional[Callable[[List[str]], bool]] = None,
    ) -> None:
        self._register_action(action_id, label, callback, enabled_when)

    def _has_selection(self, selection: List[str]) -> bool:
        return len(selection) > 0

    def _has_single_selection(self, selection: List[str]) -> bool:
        return len(selection) == 1

    def _has_single_file_selection(self, selection: List[str]) -> bool:
        return len(selection) == 1 and os.path.isfile(selection[0])

    def _can_paste(self, selection: List[str]) -> bool:
        if self.clipboard_manager and hasattr(self.clipboard_manager, "has_files"):
            return self.clipboard_manager.has_files()

        if self.file_operations and hasattr(self.file_operations, "can_paste"):
            try:
                return self.file_operations.can_paste()
            except Exception:
                return False

        return True

    def _get_target_directory_for_paste(self) -> Optional[str]:
        if not self.current_selection:
            return None

        first = self.current_selection[0]
        if os.path.isdir(first):
            return first

        return os.path.dirname(first)

    def _safe_call_file_ops(self, method_name: str, *args, **kwargs):
        if self.file_operations and hasattr(self.file_operations, method_name):
            return getattr(self.file_operations, method_name)(*args, **kwargs)
        raise AttributeError(f"file_operations no implementa '{method_name}'")

    def copy(self) -> None:
        try:
            if self.clipboard_manager and hasattr(self.clipboard_manager, "set_files"):
                self.clipboard_manager.set_files(self.current_selection, operation="copy")
                return

            self._safe_call_file_ops("copy_to_clipboard", self.current_selection)
        except Exception:
            pass

    def cut(self) -> None:
        try:
            if self.clipboard_manager and hasattr(self.clipboard_manager, "set_files"):
                self.clipboard_manager.set_files(self.current_selection, operation="cut")
                return

            self._safe_call_file_ops("cut_to_clipboard", self.current_selection)
        except Exception:
            pass

    def paste(self) -> None:
        target_dir = self._get_target_directory_for_paste()
        if not target_dir:
            return

        try:
            if self.clipboard_manager and hasattr(self.clipboard_manager, "paste"):
                self.clipboard_manager.paste(target_dir)
                return

            self._safe_call_file_ops("paste_from_clipboard", target_dir)
        except Exception:
            pass

    def delete_permanently(self) -> None:
        if not self.current_selection:
            return

        result = QMessageBox.question(
            self.parent,
            "Eliminar definitivo",
            "Esta acción eliminará definitivamente los elementos seleccionados.\n\n¿Deseas continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        for path in self.current_selection:
            try:
                if self.file_operations and hasattr(self.file_operations, "delete_permanently"):
                    self.file_operations.delete_permanently(path)
                else:
                    if os.path.isdir(path) and not os.path.islink(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
            except Exception:
                pass

    def create_shortcut(self) -> None:
        if not self.current_selection:
            return

        source = self.current_selection[0]

        try:
            if self.file_operations and hasattr(self.file_operations, "create_shortcut"):
                self.file_operations.create_shortcut(source)
                return

            self._create_shortcut_system(source)
        except Exception:
            pass

    def _create_shortcut_system(self, source: str) -> None:
        source_path = Path(source)
        shortcut_name = f"{source_path.stem} - acceso directo"

        if os.name == "nt":
            shortcut_path = source_path.parent / f"{shortcut_name}.lnk"
            script = (
                "$WScriptShell = New-Object -ComObject WScript.Shell;"
                f"$Shortcut = $WScriptShell.CreateShortcut('{str(shortcut_path)}');"
                f"$Shortcut.TargetPath = '{str(source_path)}';"
                "$Shortcut.Save();"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
        else:
            shortcut_path = source_path.parent / f"{shortcut_name}.desktop"
            content = (
                "[Desktop Entry]\n"
                "Version=1.0\n"
                "Type=Link\n"
                f"Name={shortcut_name}\n"
                f"Path={source_path.parent}\n"
                f"URL=file://{source_path}\n"
                "Icon=folder\n"
            )
            with open(shortcut_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(shortcut_path, 0o755)

    def copy_content_to_clipboard(self) -> None:
        if not self.current_selection:
            return

        file_path = self.current_selection[0]

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            QApplication.clipboard().setText(content)
        except Exception:
            pass

    def rename(self) -> None:
        if not self.current_selection:
            return

        old_path = self.current_selection[0]
        old_name = os.path.basename(old_path)
        parent_dir = os.path.dirname(old_path)

        new_name, ok = QInputDialog.getText(self.parent, "Renombrar", "Nuevo nombre:", text=old_name)
        if not ok or not new_name or new_name == old_name:
            return

        new_path = os.path.join(parent_dir, new_name)

        try:
            if self.file_operations and hasattr(self.file_operations, "rename"):
                self.file_operations.rename(old_path, new_path)
            else:
                os.rename(old_path, new_path)
        except Exception:
            pass

    def show_properties(self) -> None:
        if not self.current_selection:
            return

        lines = []
        for path in self.current_selection:
            try:
                p = Path(path)
                st = p.stat()

                lines.append(f"Ruta: {p}")
                lines.append(f"Nombre: {p.name}")
                lines.append(f"Tipo: {'Carpeta' if p.is_dir() else 'Archivo'}")
                lines.append(f"Tamaño: {st.st_size} bytes")
                lines.append(f"Permisos: {stat.filemode(st.st_mode)}")
                lines.append("")
            except Exception:
                pass

        QMessageBox.information(self.parent, "Propiedades", "\n".join(lines))

    def take_ownership(self) -> None:
        if not self.current_selection:
            return

        for path in self.current_selection:
            try:
                if self.file_operations and hasattr(self.file_operations, "take_ownership"):
                    self.file_operations.take_ownership(path)
                else:
                    self._take_ownership_system(path)
            except Exception:
                pass

    def _take_ownership_system(self, path: str) -> None:
        if os.name == "nt":
            subprocess.run(["takeown", "/F", path, "/A", "/R", "/D", "Y"], check=True, shell=True)
            subprocess.run(["icacls", path, "/grant", "Administrators:F", "/T", "/C"], check=True, shell=True)
        else:
            uid = os.getuid()
            gid = os.getgid()

            for root, dirs, files in os.walk(path):
                os.chown(root, uid, gid)
                for d in dirs:
                    os.chown(os.path.join(root, d), uid, gid)
                for f in files:
                    os.chown(os.path.join(root, f), uid, gid)

            if os.path.isfile(path):
                os.chown(path, uid, gid)