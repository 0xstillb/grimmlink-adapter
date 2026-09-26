"""Capabilities endpoint describing supported adapter features."""

from fastapi import APIRouter

from grimmlink_adapter.models.grimmlink import GrimmlinkCapabilitiesResponse

router = APIRouter(tags=["Capabilities"])

_STATIC_CAPABILITIES = GrimmlinkCapabilitiesResponse(
    apiVersion="v1",
    webUiProgress=True,
    progressSync=True,
    pdfBridge=False,
    readingSessions=True,
    metadataSync=True,
    shelves=True,
)


@router.get("/capabilities", response_model=GrimmlinkCapabilitiesResponse)
async def get_capabilities() -> GrimmlinkCapabilitiesResponse:
    """Return adapter capabilities compatible with GrimmLink client."""
    return _STATIC_CAPABILITIES
