from __future__ import annotations

from collections.abc import Callable, Iterable


class SelectionModel:
    """Renderer-independent selection shared by the tree and viewport."""

    def __init__(self) -> None:
        self.selected_ids: list[str] = []
        self.preselected_id: str | None = None
        self._listeners: list[Callable[[tuple[str, ...]], None]] = []

    def subscribe(self, listener: Callable[[tuple[str, ...]], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _notify(self) -> None:
        snapshot = tuple(self.selected_ids)
        for listener in tuple(self._listeners):
            listener(snapshot)

    def set(self, entity_ids: Iterable[str]) -> None:
        unique = list(dict.fromkeys(str(entity_id) for entity_id in entity_ids))
        if unique == self.selected_ids:
            return
        self.selected_ids = unique
        self._notify()

    def toggle(self, entity_id: str) -> None:
        selected = list(self.selected_ids)
        if entity_id in selected:
            selected.remove(entity_id)
        else:
            selected.append(entity_id)
        self.set(selected)

    def clear(self) -> None:
        self.set(())

    def remove_missing(self, valid_ids: Iterable[str]) -> None:
        valid = set(valid_ids)
        self.set(entity_id for entity_id in self.selected_ids if entity_id in valid)
        if self.preselected_id not in valid:
            self.preselected_id = None
