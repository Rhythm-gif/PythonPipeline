"""
PACR Pipeline — Pipeline Router
Exposes manual trigger endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.pipeline.service import run_pipeline
from app.common.models import ApiResponse, ServiceStatusEnum

router = APIRouter(prefix="/pipeline", tags=["Pipeline"])


@router.post("/trigger", response_model=ApiResponse)
async def trigger_now(request: Request):
    """
    Manually trigger the ingestion pipeline synchronously.
    This will block until the pipeline completes, and then return the results.
    """
    summary = await run_pipeline()
    return ApiResponse(
        status=ServiceStatusEnum.SUCCESS,
        message="Pipeline execution completed.",
        requestId=request.state.request_id,
        data={
            "total_fetched": summary.get("total_fetched"),
            "total_approved": summary.get("total_approved"),
            "total_rejected": summary.get("total_rejected"),
            "total_duplicate": summary.get("total_duplicate"),
            "total_error": summary.get("total_error"),
        },
    )
