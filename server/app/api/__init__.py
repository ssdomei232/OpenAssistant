"""API routers."""
from __future__ import annotations

from fastapi import APIRouter

from . import audio, automation, cards, commands_search, core, meetings, planner

api_router = APIRouter()
api_router.include_router(core.router)
api_router.include_router(audio.router)
api_router.include_router(cards.router)
api_router.include_router(planner.router)
api_router.include_router(meetings.router)
api_router.include_router(commands_search.router)
api_router.include_router(automation.router)

__all__ = ["api_router"]
