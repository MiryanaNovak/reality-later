"""Framing a picture: which square of it is shown (see profiles.Frame, and frame_editor.js for the browser side)."""
from __future__ import annotations

from typing import Any

from nicegui import ui

from ..profiles import Frame


class FrameEditor(ui.element, component='frame_editor.js'):
    def __init__(self, src: str, frame: Frame) -> None:
        super().__init__()
        self._props['src'] = src
        self._props['frame'] = frame.model_dump()
        self.frame = frame
        self.on('change', self._changed)

    def _changed(self, e: Any) -> None:
        args = e.args if isinstance(e.args, dict) else {}
        try:  # the browser's numbers, kept within bounds
            self.frame = Frame(x=_within(args.get('x'), 0, 1), y=_within(args.get('y'), 0, 1),
                               size=_within(args.get('size'), 0.01, 1))
        except (TypeError, ValueError):
            pass


def _within(value: Any, low: float, high: float) -> float:
    return min(max(float(value), low), high)
