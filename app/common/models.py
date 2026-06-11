from enum import Enum
from typing import Any, Generic, Optional, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")

class ServiceStatusEnum(str, Enum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"

class ApiResponse(BaseModel, Generic[T]):
    status: ServiceStatusEnum = ServiceStatusEnum.SUCCESS
    message: str = ""
    requestId: str = ""
    metaData: dict[str, Any] = Field(default_factory=dict)
    data: Optional[T] = None
