"""
file_operations.py

Responsabilidad:
- Manejar operaciones reales sobre archivos en Windows 10/11.

Incluye:
- Copiar
- Cortar
- Pegar
- Eliminar definitivo
- Renombrar
- Crear acceso directo en escritorio
- Leer contenido de archivos al portapapeles
- Obtener propiedades
- Tomar posesión (Windows API)

Notas:
- Este módulo ejecuta operaciones REALES, no simuladas.
- Está diseñado con funciones desacopladas.
- Incluye logging integrado.
- Preparado para futura adaptación async mediante funciones pequeñas y puras
  donde es posible.

Dependencias:
- Estándar: os, shutil, ctypes, logging, pathlib, tempfile, stat, time
- Windows: ctypes para shell32/user32/ole32/kernel32
- Opcional: pywin32 para creación de accesos directos más robusta.
  Si pywin32 no está instalado, se usa un fallback PowerShell real.

Compatibilidad:
- Windows 10 / 11
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union

# =========================
# Logging
# =========================

logger = logging.getLogger(__name__)

if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


# =========================
# Tipos y excepciones
# =========================

PathLike = Union[str, os.PathLike]


class FileOperationError(Exception):
    """Excepción base para errores de operaciones de archivo."""


class ClipboardError(FileOperationError):
    """Error relacionado con portapapeles."""


class ShortcutError(FileOperationError):
    """Error creando acceso directo."""


class OwnershipError(FileOperationError):
    """Error tomando posesión de archivo/carpeta."""


@dataclass
class FileProperties:
    path: str
    name: str
    exists: bool
    is_file: bool
    is_dir: bool
    size_bytes: Optional[int]
    created_ts: Optional[float]
    modified_ts: Optional[float]
    accessed_ts: Optional[float]
    is_hidden: Optional[bool]
    is_readonly: Optional[bool]

    def to_dict(self) -> dict:
        return asdict(self)


# =========================
# Estado interno clipboard operativo
# =========================

# Nota:
# Esto no reemplaza el portapapeles del sistema para copiar/cortar archivos
# al estilo Explorer. Aquí mantenemos un buffer interno real para luego pegar.
# Es una implementación real de operaciones de FS.
_CLIPBOARD_FILE_PATHS: List[str] = []
_CLIPBOARD_OPERATION: Optional[str] = None  # "copy" | "cut"


# =========================
# Helpers generales
# =========================

def _ensure_windows() -> None:
    if os.name != "nt":
        raise OSError("Este módulo está diseñado para Windows.")


def _to_path(path: PathLike) -> Path:
    return Path(path).expanduser().resolve()


def _validate_exists(path: PathLike) -> Path:
    p = _to_path(path)
    if not p.exists():
        raise FileNotFoundError(f"No existe la ruta: {p}")
    return p


def _validate_many_exist(paths: Sequence[PathLike]) -> List[Path]:
    if not paths:
        raise ValueError("La lista de rutas no puede estar vacía.")
    return [_validate_exists(p) for p in paths]


def _ensure_directory(path: PathLike) -> Path:
    p = _to_path(path)
    if not p.exists():
        raise FileNotFoundError(f"El directorio no existe: {p}")
    if not p.is_dir():
        raise NotADirectoryError(f"No es un directorio: {p}")
    return p


def _make_unique_destination(path: Path) -> Path:
    """
    Si el destino existe, genera uno nuevo:
    archivo.txt -> archivo (1).txt
    carpeta -> carpeta (1)
    """
    if not path.exists():
        return path

    parent = path.parent
    stem = path.stem
    suffix = path.suffix

    counter = 1
    while True:
        if suffix:
            candidate = parent / f"{stem} ({counter}){suffix}"
        else:
            candidate = parent / f"{path.name} ({counter})"
        if not candidate.exists():
            return candidate
        counter += 1


def _copy_item(src: Path, dst: Path) -> Path:
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return dst


def _move_item(src: Path, dst: Path) -> Path:
    result = shutil.move(str(src), str(dst))
    return Path(result)


def _remove_readonly(func, path, excinfo):
    """
    Callback para shutil.rmtree al encontrar atributos readonly.
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        logger.exception("No se pudo remover atributo readonly en %s", path)
        raise FileOperationError(f"No se pudo eliminar {path}: {e}") from e


def _delete_item(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, onerror=_remove_readonly)
    else:
        if not os.access(path, os.W_OK):
            os.chmod(path, stat.S_IWRITE)
        path.unlink()


def _desktop_path() -> Path:
    # Desktop del usuario actual
    desktop = Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))
    return desktop


def _powershell_escape(value: str) -> str:
    return value.replace("'", "''")


# =========================
# Operaciones clipboard interno
# =========================

def copy_to_clipboard(paths: Sequence[PathLike]) -> List[str]:
    """
    Guarda rutas para operación de copiado posterior con paste_from_clipboard().
    """
    global _CLIPBOARD_FILE_PATHS, _CLIPBOARD_OPERATION

    resolved = [str(p) for p in _validate_many_exist(paths)]
    _CLIPBOARD_FILE_PATHS = resolved
    _CLIPBOARD_OPERATION = "copy"

    logger.info("Copiado al clipboard interno: %s", resolved)
    return resolved


def cut_to_clipboard(paths: Sequence[PathLike]) -> List[str]:
    """
    Guarda rutas para operación de corte posterior con paste_from_clipboard().
    """
    global _CLIPBOARD_FILE_PATHS, _CLIPBOARD_OPERATION

    resolved = [str(p) for p in _validate_many_exist(paths)]
    _CLIPBOARD_FILE_PATHS = resolved
    _CLIPBOARD_OPERATION = "cut"

    logger.info("Cortado al clipboard interno: %s", resolved)
    return resolved


def get_clipboard_file_operation() -> dict:
    return {
        "paths": list(_CLIPBOARD_FILE_PATHS),
        "operation": _CLIPBOARD_OPERATION,
    }


def clear_clipboard_file_operation() -> None:
    global _CLIPBOARD_FILE_PATHS, _CLIPBOARD_OPERATION
    _CLIPBOARD_FILE_PATHS = []
    _CLIPBOARD_OPERATION = None
    logger.info("Clipboard interno limpiado.")


def paste_from_clipboard(destination_dir: PathLike) -> List[str]:
    """
    Ejecuta la operación pendiente del clipboard interno.
    - copy => copia
    - cut => mueve
    """
    global _CLIPBOARD_FILE_PATHS, _CLIPBOARD_OPERATION

    dst_dir = _ensure_directory(destination_dir)

    if not _CLIPBOARD_FILE_PATHS or _CLIPBOARD_OPERATION not in {"copy", "cut"}:
        raise FileOperationError("No hay operación pendiente en el clipboard interno.")

    created_paths: List[str] = []

    try:
        for raw_src in _CLIPBOARD_FILE_PATHS:
            src = _validate_exists(raw_src)
            candidate = _make_unique_destination(dst_dir / src.name)

            if _CLIPBOARD_OPERATION == "copy":
                final_path = _copy_item(src, candidate)
            else:
                final_path = _move_item(src, candidate)

            created_paths.append(str(final_path))
            logger.info(
                "Pegado completado: op=%s src=%s dst=%s",
                _CLIPBOARD_OPERATION,
                src,
                final_path,
            )

        if _CLIPBOARD_OPERATION == "cut":
            clear_clipboard_file_operation()

        return created_paths

    except Exception as e:
        logger.exception("Error pegando desde clipboard hacia %s", dst_dir)
        raise FileOperationError(f"Error al pegar elementos en {dst_dir}: {e}") from e


# =========================
# Copiar / cortar / pegar directos
# =========================

def copy_items(paths: Sequence[PathLike], destination_dir: PathLike) -> List[str]:
    src_paths = _validate_many_exist(paths)
    dst_dir = _ensure_directory(destination_dir)

    results: List[str] = []
    try:
        for src in src_paths:
            dst = _make_unique_destination(dst_dir / src.name)
            final_path = _copy_item(src, dst)
            results.append(str(final_path))
            logger.info("Copiado: %s -> %s", src, final_path)
        return results
    except Exception as e:
        logger.exception("Error copiando elementos a %s", dst_dir)
        raise FileOperationError(f"Error copiando elementos a {dst_dir}: {e}") from e


def move_items(paths: Sequence[PathLike], destination_dir: PathLike) -> List[str]:
    src_paths = _validate_many_exist(paths)
    dst_dir = _ensure_directory(destination_dir)

    results: List[str] = []
    try:
        for src in src_paths:
            dst = _make_unique_destination(dst_dir / src.name)
            final_path = _move_item(src, dst)
            results.append(str(final_path))
            logger.info("Movido: %s -> %s", src, final_path)
        return results
    except Exception as e:
        logger.exception("Error moviendo elementos a %s", dst_dir)
        raise FileOperationError(f"Error moviendo elementos a {dst_dir}: {e}") from e


# =========================
# Eliminar definitivo
# =========================

def delete_permanently(paths: Sequence[PathLike]) -> List[str]:
    src_paths = _validate_many_exist(paths)
    deleted: List[str] = []

    try:
        for path in src_paths:
            _delete_item(path)
            deleted.append(str(path))
            logger.info("Eliminado definitivamente: %s", path)
        return deleted
    except Exception as e:
        logger.exception("Error eliminando elementos definitivamente")
        raise FileOperationError(f"Error eliminando elementos: {e}") from e


# =========================
# Renombrar
# =========================

def rename_item(path: PathLike, new_name: str) -> str:
    if not new_name or any(c in new_name for c in '<>:"/\\|?*'):
        raise ValueError(f"Nombre inválido para Windows: {new_name}")

    src = _validate_exists(path)
    dst = src.with_name(new_name)

    if dst.exists():
        raise FileExistsError(f"Ya existe un elemento con el nombre destino: {dst}")

    try:
        src.rename(dst)
        logger.info("Renombrado: %s -> %s", src, dst)
        return str(dst)
    except Exception as e:
        logger.exception("Error renombrando %s", src)
        raise FileOperationError(f"Error renombrando {src} a {new_name}: {e}") from e


# =========================
# Acceso directo en escritorio
# =========================

def create_desktop_shortcut(
    target_path: PathLike,
    shortcut_name: Optional[str] = None,
    arguments: str = "",
    working_directory: Optional[PathLike] = None,
    icon_path: Optional[PathLike] = None,
    description: str = "",
) -> str:
    """
    Crea un acceso directo .lnk real en el escritorio.

    Estrategia:
    1. Intentar con pywin32 si está disponible.
    2. Fallback con PowerShell + WScript.Shell.
    """
    _ensure_windows()
    target = _validate_exists(target_path)
    desktop = _desktop_path()

    if shortcut_name is None:
        shortcut_name = target.stem

    shortcut_file = desktop / f"{shortcut_name}.lnk"
    shortcut_file = _make_unique_destination(shortcut_file)

    wd = (
        str(_to_path(working_directory))
        if working_directory
        else str(target.parent)
    )
    icon = str(_to_path(icon_path)) if icon_path else str(target)

    # Intento pywin32
    try:
        import win32com.client  # type: ignore

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(shortcut_file))
        shortcut.TargetPath = str(target)
        shortcut.Arguments = arguments
        shortcut.WorkingDirectory = wd
        shortcut.IconLocation = icon
        shortcut.Description = description
        shortcut.save()

        logger.info("Acceso directo creado con pywin32: %s", shortcut_file)
        return str(shortcut_file)

    except ImportError:
        logger.info("pywin32 no disponible; usando fallback PowerShell.")
    except Exception as e:
        logger.warning("Falló pywin32 creando shortcut, usando fallback: %s", e)

    # Fallback PowerShell real
    try:
        ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{_powershell_escape(str(shortcut_file))}')
$Shortcut.TargetPath = '{_powershell_escape(str(target))}'
$Shortcut.Arguments = '{_powershell_escape(arguments)}'
$Shortcut.WorkingDirectory = '{_powershell_escape(wd)}'
$Shortcut.IconLocation = '{_powershell_escape(icon)}'
$Shortcut.Description = '{_powershell_escape(description)}'
$Shortcut.Save()
"""
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise ShortcutError(
                f"PowerShell devolvió código {completed.returncode}: {completed.stderr.strip()}"
            )

        if not shortcut_file.exists():
            raise ShortcutError("El acceso directo no fue creado.")

        logger.info("Acceso directo creado con PowerShell: %s", shortcut_file)
        return str(shortcut_file)

    except Exception as e:
        logger.exception("Error creando acceso directo para %s", target)
        raise ShortcutError(f"No se pudo crear el acceso directo: {e}") from e


# =========================
# Portapapeles de texto Windows
# =========================

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _open_clipboard() -> None:
    user32 = ctypes.windll.user32
    for _ in range(10):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise ClipboardError("No se pudo abrir el portapapeles.")


def _close_clipboard() -> None:
    ctypes.windll.user32.CloseClipboard()


def _set_clipboard_text(text: str) -> None:
    """
    Escribe texto Unicode real al portapapeles de Windows.
    """
    _ensure_windows()

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    msvcrt = ctypes.cdll.msvcrt

    data = text.encode("utf-16le") + b"\x00\x00"
    h_global = None

    try:
        _open_clipboard()
        user32.EmptyClipboard()

        h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h_global:
            raise ClipboardError("GlobalAlloc falló.")

        p_global = kernel32.GlobalLock(h_global)
        if not p_global:
            kernel32.GlobalFree(h_global)
            raise ClipboardError("GlobalLock falló.")

        ctypes.memmove(p_global, data, len(data))
        kernel32.GlobalUnlock(h_global)

        if not user32.SetClipboardData(CF_UNICODETEXT, h_global):
            kernel32.GlobalFree(h_global)
            raise ClipboardError("SetClipboardData falló.")

        # Ownership transferido al sistema si SetClipboardData tiene éxito.
        h_global = None
        logger.info("Texto copiado al portapapeles del sistema.")

    except Exception as e:
        logger.exception("Error escribiendo al portapapeles")
        raise ClipboardError(f"No se pudo escribir en el portapapeles: {e}") from e
    finally:
        if h_global:
            try:
                kernel32.GlobalFree(h_global)
            except Exception:
                pass
        try:
            _close_clipboard()
        except Exception:
            pass


def copy_file_content_to_clipboard(path: PathLike, encoding: str = "utf-8") -> str:
    """
    Lee el contenido real de un archivo de texto y lo coloca en el portapapeles.
    """
    file_path = _validate_exists(path)
    if not file_path.is_file():
        raise IsADirectoryError(f"La ruta no es un archivo: {file_path}")

    try:
        content = file_path.read_text(encoding=encoding)
        _set_clipboard_text(content)
        logger.info("Contenido de archivo copiado al portapapeles: %s", file_path)
        return content
    except UnicodeDecodeError as e:
        logger.exception("Error de encoding leyendo %s", file_path)
        raise FileOperationError(
            f"No se pudo leer el archivo con encoding '{encoding}': {e}"
        ) from e
    except Exception as e:
        logger.exception("Error copiando contenido al portapapeles desde %s", file_path)
        raise FileOperationError(
            f"No se pudo copiar el contenido de {file_path} al portapapeles: {e}"
        ) from e


# =========================
# Propiedades
# =========================

def get_file_properties(path: PathLike) -> FileProperties:
    p = _to_path(path)
    exists = p.exists()

    if not exists:
        props = FileProperties(
            path=str(p),
            name=p.name,
            exists=False,
            is_file=False,
            is_dir=False,
            size_bytes=None,
            created_ts=None,
            modified_ts=None,
            accessed_ts=None,
            is_hidden=None,
            is_readonly=None,
        )
        logger.info("Propiedades consultadas para ruta inexistente: %s", p)
        return props

    try:
        stat_result = p.stat()
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(p))
        hidden = None if attrs == -1 else bool(attrs & 0x2)
        readonly = None if attrs == -1 else bool(attrs & 0x1)

        props = FileProperties(
            path=str(p),
            name=p.name,
            exists=True,
            is_file=p.is_file(),
            is_dir=p.is_dir(),
            size_bytes=stat_result.st_size if p.is_file() else None,
            created_ts=stat_result.st_ctime,
            modified_ts=stat_result.st_mtime,
            accessed_ts=stat_result.st_atime,
            is_hidden=hidden,
            is_readonly=readonly,
        )
        logger.info("Propiedades obtenidas: %s", p)
        return props

    except Exception as e:
        logger.exception("Error obteniendo propiedades de %s", p)
        raise FileOperationError(f"No se pudieron obtener propiedades de {p}: {e}") from e


# =========================
# Tomar posesión
# =========================

def take_ownership(path: PathLike, recursive: bool = True) -> bool:
    """
    Toma posesión real del archivo/carpeta usando la herramienta nativa de Windows
    'takeown', que usa Windows API internamente.

    Requiere permisos adecuados; en muchos casos necesita elevación.
    """
    _ensure_windows()
    target = _validate_exists(path)

    cmd = ["takeown", "/F", str(target)]
    if target.is_dir() and recursive:
        cmd.extend(["/R", "/D", "Y"])

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            message = stderr or stdout or "takeown falló sin detalle."
            raise OwnershipError(message)

        logger.info("Posesión tomada sobre: %s", target)
        return True

    except Exception as e:
        logger.exception("Error tomando posesión de %s", target)
        raise OwnershipError(f"No se pudo tomar posesión de {target}: {e}") from e


def grant_full_control_to_current_user(path: PathLike, recursive: bool = True) -> bool:
    """
    Opcional pero útil tras take_ownership:
    concede control total al usuario actual mediante icacls.
    """
    _ensure_windows()
    target = _validate_exists(path)
    username = os.getlogin()

    cmd = ["icacls", str(target), "/grant", f"{username}:F"]
    if target.is_dir() and recursive:
        cmd.append("/T")

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            message = stderr or stdout or "icacls falló sin detalle."
            raise OwnershipError(message)

        logger.info("Control total concedido a %s sobre %s", username, target)
        return True

    except Exception as e:
        logger.exception("Error otorgando control total sobre %s", target)
        raise OwnershipError(
            f"No se pudo otorgar control total sobre {target}: {e}"
        ) from e


# =========================
# Utilidades async-ready
# =========================

def is_operation_pending() -> bool:
    return bool(_CLIPBOARD_FILE_PATHS and _CLIPBOARD_OPERATION)


def ensure_parent_directory(path: PathLike) -> str:
    p = _to_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Directorio padre asegurado para %s", p)
    return str(p.parent)


# =========================
# API pública
# =========================

__all__ = [
    "FileOperationError",
    "ClipboardError",
    "ShortcutError",
    "OwnershipError",
    "FileProperties",
    "copy_to_clipboard",
    "cut_to_clipboard",
    "get_clipboard_file_operation",
    "clear_clipboard_file_operation",
    "paste_from_clipboard",
    "copy_items",
    "move_items",
    "delete_permanently",
    "rename_item",
    "create_desktop_shortcut",
    "copy_file_content_to_clipboard",
    "get_file_properties",
    "take_ownership",
    "grant_full_control_to_current_user",
    "is_operation_pending",
    "ensure_parent_directory",
]