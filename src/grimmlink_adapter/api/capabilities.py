"""Capabilities endpoint describing supported adapter features."""

from fastapi import APIRouter

from grimmlink_adapter.models.grimmlink import GrimmlinkCapabilitiesResponse

router = APIRouter(tags=["Capabilities"])

# Session 00 exposes route shapes, but no sync or shelf data paths are operational.
_STATIC_CAPABILITIES = GrimmlinkCapabilitiesResponse(
    apiVersion="v1",
    webUiProgress=False,
    progressSync=False,
    pdfBridge=False,
    readingSessions=False,
    metadataSync=False,
    shelves=False,
)


@router.get("/capabilities", response_model=GrimmlinkCapabilitiesResponse)
async def get_capabilities() -> GrimmlinkCapabilitiesResponse:
    """Return adapter capabilities compatible with GrimmLink client."""
    return _STATIC_CAPABILITIES
