from __future__ import annotations

from fastapi import BackgroundTasks, Request

from app.core.errors import AppError
from app.services.resources import Resources
from app.services.task_queue import BackgroundTasksQueue, TaskQueue


def get_resources(request: Request) -> Resources:
    resources = getattr(request.app.state, "resources", None)
    if resources is None:
        raise AppError("MODELS_NOT_LOADED", "NLP models are not loaded yet — try again in a few seconds", 503)
    return resources


def get_queue(background_tasks: BackgroundTasks) -> TaskQueue:
    return BackgroundTasksQueue(background_tasks)
