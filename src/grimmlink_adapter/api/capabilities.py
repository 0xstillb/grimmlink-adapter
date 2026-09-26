"""Capabilities endpoint describing supported adapter features."""

from fastapi import APIRouter

from grimmlink_adapter.models.grimmlink import GrimmlinkCapabilitiesResponse

router = APIRouter(tags=["Capabilities"])

# In Session 00 (scaffold), mutations are not yet enabled in Official Grimmory.
# Capabilities explicitly reflect what is currently operational.
_STATIC_CAPABILITIES = GrimmlinkCapabilitiesResponse(
    apiVersion="v1",
    webUiProgress=False,
    progressSync=False,
    pdfBridge=False,
    readingSessions=False,
    metadataSync=False,
    shelves=True,  # Read-only shelf queries supported in scaffold
)


@router.get("/capabilities", response_model=GrimmlinkCapabilitiesResponse)
async def get_capabilities() -> GrimmlinkCapabilitiesResponse:
    """Return adapter capabilities compatible with GrimmLink client."""
    return _STATIC_CAPABILITIES
