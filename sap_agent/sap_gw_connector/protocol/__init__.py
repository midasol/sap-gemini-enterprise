"""SAP Gateway Protocol definitions"""

from .schemas import (
    GWError,
    GWMethodType,
    GWRequest,
    GWResponse,
    ToolCallRequest,
    ToolCallResponse,
    ToolInfo,
    ListToolsResponse,
    HealthResponse,
)

__all__ = [
    "GWError",
    "GWMethodType",
    "GWRequest",
    "GWResponse",
    "ToolCallRequest",
    "ToolCallResponse",
    "ToolInfo",
    "ListToolsResponse",
    "HealthResponse",
]
