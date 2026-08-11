"""
PACR Pipeline — Pipeline Router
Exposes manual trigger endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, BackgroundTasks

from app.pipeline.service import run_pipeline
from app.common.models import ApiResponse, ServiceStatusEnum

router = APIRouter(prefix="/pipeline", tags=["Pipeline"])


@router.post("/trigger", response_model=ApiResponse)
async def trigger_now(request: Request, background_tasks: BackgroundTasks):
    """
    Manually trigger the ingestion pipeline asynchronously in the background.
    This returns immediately to prevent Load Balancer timeouts (502/504),
    while the heavy lifting runs in the background.
    """
    background_tasks.add_task(run_pipeline)
    return ApiResponse(
        status=ServiceStatusEnum.SUCCESS,
        message="Pipeline triggered successfully in the background!",
        requestId=request.state.request_id,
        data={
            "info": "Check server logs to monitor the background execution."
        },
    )
