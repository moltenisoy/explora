from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple


class ViewMode(str, Enum):
    ICONS = "icons"
    DETAILS = "details"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


@dataclass(slots=True)
class FileItem:
    """
    Modelo desacoplado de la UI para representar un archivo o carpeta.
    """
    path: Path
    name: str
    size: int
    extension: str
    created_at: Optional[datetime]
    modified_at: Optional[datetime]
    is_directory: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ColumnDefinition:
    """
    Define una columna para vistas tabulares.
    """
    key: str
    title: str
    sortable: bool = True
    width: Optional[int] = None
    visible: bool = True


@dataclass(slots=True)
class ViewState:
    """
    Estado serializable y consumible por cualquier UI.
    """
    mode: ViewMode
    items: List[FileItem]
    selected_paths: Set[Path]
    sort_by: Optional[str]
    sort_direction: SortDirection
    columns: List[ColumnDefinition]
    extra: Dict[str, Any] = field(default_factory=dict)


class SelectionManager:
    """
    Maneja selección múltiple de manera desacoplada de cualquier toolkit UI.
    """

    def __init__(self) -> None:
        self._selected: Set[Path] = set()
        self._anchor: Optional[Path] = None

    def get_selected(self) -> Set[Path]:
        return set(self._selected)

    def clear(self) -> None:
        self._selected.clear()
        self._anchor = None

    def set_single(self, item_path: Path) -> None:
        self._selected = {item_path}
        self._anchor = item_path

    def toggle(self, item_path: Path) -> None:
        if item_path in self._selected:
            self._selected.remove(item_path)
        else:
            self._selected.add(item_path)
            self._anchor = item_path

    def set_multiple(self, item_paths: Iterable[Path]) -> None:
        paths = set(item_paths)
        self._selected = paths
        if paths:
            self._anchor = next(iter(paths))

    def range_select(
        self,
        ordered_items: Sequence[FileItem],
        target_path: Path,
    ) -> None:
        if not ordered_items:
            return

        if self._anchor is None:
            self.set_single(target_path)
            return

        indexed_paths = [item.path for item in ordered_items]
        try:
            start = indexed_paths.index(self._anchor)
            end = indexed_paths.index(target_path)
        except ValueError:
            self.set_single(target_path)
            return

        if start > end:
            start, end = end, start

        self._selected = set(indexed_paths[start:end + 1])

    def is_selected(self, item_path: Path) -> bool:
        return item_path in self._selected


class BaseViewAdapter(ABC):
    """
    Adaptador base para desacoplar lógica de vista de la UI concreta.

    Una UI real puede implementar estos métodos para renderizar
    escritorio, tabla, virtualización, etc.
    """

    @property
    @abstractmethod
    def mode(self) -> ViewMode:
        raise NotImplementedError

    @abstractmethod
    def build_payload(self, state: ViewState) -> Dict[str, Any]:
        """
        Retorna un payload consumible por la capa UI.
        """
        raise NotImplementedError


class IconsViewAdapter(BaseViewAdapter):
    """
    Vista tipo escritorio con iconos libres.
    """

    @property
    def mode(self) -> ViewMode:
        return ViewMode.ICONS

    def build_payload(self, state: ViewState) -> Dict[str, Any]:
        return {
            "mode": state.mode.value,
            "items": [
                {
                    "path": str(item.path),
                    "name": item.name,
                    "is_directory": item.is_directory,
                    "selected": item.path in state.selected_paths,
                    "metadata": item.metadata,
                }
                for item in state.items
            ],
            "selection": [str(path) for path in state.selected_paths],
            "sort_by": state.sort_by,
            "sort_direction": state.sort_direction.value,
            "layout": {
                "type": "free-icons",
                "virtualization_recommended": True,
            },
        }


class DetailsViewAdapter(BaseViewAdapter):
    """
    Vista detallada con columnas y soporte de ordenamiento.
    """

    DEFAULT_COLUMNS: Tuple[ColumnDefinition, ...] = (
        ColumnDefinition(key="name", title="Nombre", width=280),
        ColumnDefinition(key="size", title="Tamaño", width=120),
        ColumnDefinition(key="extension", title="Extensión", width=120),
        ColumnDefinition(key="created_at", title="Fecha creación", width=180),
        ColumnDefinition(key="modified_at", title="Última modificación", width=180),
    )

    @property
    def mode(self) -> ViewMode:
        return ViewMode.DETAILS

    def build_payload(self, state: ViewState) -> Dict[str, Any]:
        return {
            "mode": state.mode.value,
            "columns": [
                {
                    "key": col.key,
                    "title": col.title,
                    "sortable": col.sortable,
                    "width": col.width,
                    "visible": col.visible,
                }
                for col in state.columns
            ],
            "rows": [
                {
                    "path": str(item.path),
                    "selected": item.path in state.selected_paths,
                    "cells": {
                        "name": item.name,
                        "size": item.size,
                        "extension": item.extension,
                        "created_at": _dt_to_iso(item.created_at),
                        "modified_at": _dt_to_iso(item.modified_at),
                    },
                    "is_directory": item.is_directory,
                    "metadata": item.metadata,
                }
                for item in state.items
            ],
            "sort_by": state.sort_by,
            "sort_direction": state.sort_direction.value,
            "performance": {
                "virtualization_recommended": True,
                "stable_row_key": "path",
            },
        }


class ViewsManager:
    """
    Maneja vistas de archivos de forma desacoplada de la UI.

    Responsabilidades:
    - Cambio dinámico entre vista de iconos y vista detallada
    - Ordenamiento por columnas
    - Integración con selección múltiple
    - Preparado para sumar nuevas vistas
    - Eficiente para carpetas grandes:
        - ordenamiento centralizado
        - snapshots inmutables de estado
        - adaptadores de salida livianos
    """

    def __init__(
        self,
        items: Optional[Iterable[FileItem]] = None,
        initial_mode: ViewMode = ViewMode.ICONS,
        adapters: Optional[Iterable[BaseViewAdapter]] = None,
    ) -> None:
        self._items: List[FileItem] = list(items or [])
        self._selection = SelectionManager()
        self._mode = initial_mode
        self._sort_by: Optional[str] = None
        self._sort_direction = SortDirection.ASC
        self._state_listeners: List[Callable[[ViewState], None]] = []

        default_adapters: List[BaseViewAdapter] = [
            IconsViewAdapter(),
            DetailsViewAdapter(),
        ]
        adapter_list = list(adapters) if adapters is not None else default_adapters
        self._adapters: Dict[ViewMode, BaseViewAdapter] = {
            adapter.mode: adapter for adapter in adapter_list
        }

        self._columns_by_mode: Dict[ViewMode, List[ColumnDefinition]] = {
            ViewMode.ICONS: [],
            ViewMode.DETAILS: [ColumnDefinition(**vars(col)) for col in DetailsViewAdapter.DEFAULT_COLUMNS],
        }

    # -------------------------------------------------------------------------
    # Gestión de datos
    # -------------------------------------------------------------------------

    def set_items(self, items: Iterable[FileItem], preserve_selection: bool = True) -> None:
        new_items = list(items)
        self._items = new_items

        if preserve_selection:
            valid_paths = {item.path for item in new_items}
            current = self._selection.get_selected()
            self._selection.set_multiple(path for path in current if path in valid_paths)
        else:
            self._selection.clear()

        self._emit_state()

    def append_items(self, items: Iterable[FileItem]) -> None:
        self._items.extend(items)
        self._emit_state()

    def clear_items(self) -> None:
        self._items.clear()
        self._selection.clear()
        self._emit_state()

    def get_items(self) -> List[FileItem]:
        return list(self._items)

    # -------------------------------------------------------------------------
    # Gestión de vistas
    # -------------------------------------------------------------------------

    def register_adapter(self, adapter: BaseViewAdapter, columns: Optional[List[ColumnDefinition]] = None) -> None:
        """
        Permite agregar nuevas vistas en el futuro.
        """
        self._adapters[adapter.mode] = adapter
        if columns is not None:
            self._columns_by_mode[adapter.mode] = columns

    def set_view_mode(self, mode: ViewMode) -> None:
        if mode not in self._adapters:
            raise ValueError(f"No hay adaptador registrado para la vista '{mode}'.")
        self._mode = mode
        self._emit_state()

    def toggle_view_mode(self) -> None:
        self._mode = ViewMode.DETAILS if self._mode == ViewMode.ICONS else ViewMode.ICONS
        self._emit_state()

    def get_view_mode(self) -> ViewMode:
        return self._mode

    def get_available_modes(self) -> List[ViewMode]:
        return list(self._adapters.keys())

    # -------------------------------------------------------------------------
    # Ordenamiento
    # -------------------------------------------------------------------------

    def sort_by_column(self, column_key: str) -> None:
        """
        Simula click sobre el header:
        - si se hace click sobre la misma columna, alterna ASC/DESC
        - si es otra columna, ordena ASC
        """
        valid_columns = {col.key for col in self._columns_by_mode.get(ViewMode.DETAILS, []) if col.sortable}
        if column_key not in valid_columns:
            raise ValueError(f"Columna no ordenable o inexistente: '{column_key}'")

        if self._sort_by == column_key:
            self._sort_direction = (
                SortDirection.DESC
                if self._sort_direction == SortDirection.ASC
                else SortDirection.ASC
            )
        else:
            self._sort_by = column_key
            self._sort_direction = SortDirection.ASC

        self._emit_state()

    def set_sort(self, column_key: Optional[str], direction: SortDirection = SortDirection.ASC) -> None:
        self._sort_by = column_key
        self._sort_direction = direction
        self._emit_state()

    def get_sort(self) -> Tuple[Optional[str], SortDirection]:
        return self._sort_by, self._sort_direction

    # -------------------------------------------------------------------------
    # Selección múltiple
    # -------------------------------------------------------------------------

    def clear_selection(self) -> None:
        self._selection.clear()
        self._emit_state()

    def select_one(self, item_path: Path) -> None:
        self._selection.set_single(item_path)
        self._emit_state()

    def toggle_selection(self, item_path: Path) -> None:
        self._selection.toggle(item_path)
        self._emit_state()

    def select_many(self, item_paths: Iterable[Path]) -> None:
        self._selection.set_multiple(item_paths)
        self._emit_state()

    def range_select(self, target_path: Path) -> None:
        ordered = self._get_sorted_items()
        self._selection.range_select(ordered, target_path)
        self._emit_state()

    def get_selected_paths(self) -> Set[Path]:
        return self._selection.get_selected()

    def is_selected(self, item_path: Path) -> bool:
        return self._selection.is_selected(item_path)

    # -------------------------------------------------------------------------
    # Estado / Render desacoplado
    # -------------------------------------------------------------------------

    def subscribe(self, listener: Callable[[ViewState], None]) -> Callable[[], None]:
        self._state_listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._state_listeners:
                self._state_listeners.remove(listener)

        return unsubscribe

    def get_state(self) -> ViewState:
        sorted_items = self._get_sorted_items()
        columns = self._columns_by_mode.get(self._mode, [])
        return ViewState(
            mode=self._mode,
            items=sorted_items,
            selected_paths=self._selection.get_selected(),
            sort_by=self._sort_by,
            sort_direction=self._sort_direction,
            columns=[ColumnDefinition(**vars(col)) for col in columns],
            extra={
                "total_items": len(sorted_items),
                "selected_count": len(self._selection.get_selected()),
                "supports_multi_select": True,
            },
        )

    def build_view_payload(self) -> Dict[str, Any]:
        state = self.get_state()
        adapter = self._adapters[self._mode]
        return adapter.build_payload(state)

    # -------------------------------------------------------------------------
    # Internals
    # -------------------------------------------------------------------------

    def _emit_state(self) -> None:
        if not self._state_listeners:
            return

        state = self.get_state()
        for listener in list(self._state_listeners):
            listener(state)

    def _get_sorted_items(self) -> List[FileItem]:
        if not self._sort_by:
            return list(self._items)

        reverse = self._sort_direction == SortDirection.DESC
        return sorted(
            self._items,
            key=lambda item: self._build_sort_key(item, self._sort_by),
            reverse=reverse,
        )

    @staticmethod
    def _build_sort_key(item: FileItem, column_key: str) -> Tuple[int, Any]:
        """
        Clave robusta y estable:
        - Carpetas primero
        - Nulos al final
        - Comparables homogéneos
        """
        directory_rank = 0 if item.is_directory else 1

        value_map = {
            "name": item.name.casefold(),
            "size": item.size,
            "extension": item.extension.casefold(),
            "created_at": item.created_at or datetime.min,
            "modified_at": item.modified_at or datetime.min,
        }

        if column_key not in value_map:
            return (directory_rank, item.name.casefold())

        value = value_map[column_key]
        return (directory_rank, value, item.name.casefold())


def _dt_to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


if __name__ == "__main__":
    # Ejemplo de uso simple y desacoplado de cualquier UI concreta.
    sample_items = [
        FileItem(
            path=Path("/docs"),
            name="docs",
            size=0,
            extension="",
            created_at=datetime(2026, 1, 10, 9, 0, 0),
            modified_at=datetime(2026, 3, 20, 18, 0, 0),
            is_directory=True,
        ),
        FileItem(
            path=Path("/report.pdf"),
            name="report.pdf",
            size=248000,
            extension=".pdf",
            created_at=datetime(2026, 2, 1, 12, 0, 0),
            modified_at=datetime(2026, 3, 30, 8, 15, 0),
        ),
        FileItem(
            path=Path("/image.png"),
            name="image.png",
            size=10240,
            extension=".png",
            created_at=datetime(2026, 1, 15, 10, 45, 0),
            modified_at=datetime(2026, 3, 25, 14, 30, 0),
        ),
    ]

    manager = ViewsManager(sample_items, initial_mode=ViewMode.DETAILS)

    manager.sort_by_column("name")
    manager.select_one(Path("/report.pdf"))
    manager.toggle_selection(Path("/image.png"))

    details_payload = manager.build_view_payload()
    print("DETAILS VIEW")
    print(details_payload)

    manager.set_view_mode(ViewMode.ICONS)
    icons_payload = manager.build_view_payload()
    print("\nICONS VIEW")
    print(icons_payload)