"""Serves stored images to the browser at /media/<collection>/<file name>."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException
from fastapi.responses import FileResponse
from nicegui import app

from ..images import ImageStore

_ROUTE = '/media/{collection}/{name}'
_STORES: dict[str, ImageStore] = {}


def _serve(collection: str, name: str) -> FileResponse:
    store = _STORES.get(collection)
    path = store.path(name) if store else None
    if path is None:
        raise HTTPException(status_code=404)
    # File names come from the content, so a name always means the same picture: cache it for good.
    return FileResponse(path, headers={'Cache-Control': 'public, max-age=31536000, immutable'})


def serve_images(collection: str, images: ImageStore) -> Callable[[str], str]:
    """Make the images reachable by the browser; returns a function giving an image's URL ('' for none)."""
    _STORES[collection] = images
    ensure_route()
    return lambda name: f'/media/{collection}/{name}' if name else ''


def ensure_route() -> None:
    """Add the /media route if it isn't there (also called for every page, in case something removed it)."""
    if not any(getattr(route, 'path', None) == _ROUTE for route in app.routes):
        app.add_api_route(_ROUTE, _serve, methods=['GET'])
