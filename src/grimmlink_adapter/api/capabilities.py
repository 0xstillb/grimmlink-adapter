"""Capabilities endpoint describing supported adapter features."""

from fastapi import APIRouter, Request

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.grimmlink import GrimmlinkCapabilitiesResponse

router = APIRouter(tags=["Capabilities"])

# Progress normalization and the Official KOReader bridge are operational.
_STATIC_CAPABILITIES = GrimmlinkCapabilitiesResponse(
    apiVersion="v1",
    webUiProgress=True,
    progressSync=True,
    pdfBridge=True,
    readingSessions=True,
    metadataSync=True,
    shelves=True,
)


@router.get(
    "/capabilities",
    response_model=GrimmlinkCapabilitiesResponse,
    response_model_exclude_none=True,
)
async def get_capabilities(request: Request) -> GrimmlinkCapabilitiesResponse:
    """Return adapter capabilities compatible with GrimmLink client."""
    if "/v2/" in request.url.path:
        db_password = settings.GRIMMORY_DB_PASSWORD
        hash_lookup_configured = bool(
            settings.GRIMMORY_DB_ENABLED
            and settings.GRIMMORY_DB_HOST
            and settings.GRIMMORY_DB_HOST.strip()
            and settings.GRIMMORY_DB_USER
            and settings.GRIMMORY_DB_USER.strip()
            and settings.GRIMMORY_DB_NAME.strip()
            and db_password
            and db_password.get_secret_value()
        )
        return _STATIC_CAPABILITIES.model_copy(update={
            "apiVersion": "v2",
            "features": {
                "readers": ["koreader", "readest"],
                "formats": ["EPUB", "PDF", "CBZ", "CBR", "DJVU", "FB2", "MOBI", "AZW3"],
                "positionTypes": ["epub-cfi", "koreader-xpointer", "pdf-page"],
                "readingSessions": True,
                "regularShelfList": True,
                "regularShelfMembershipWrite": True,
                "magicShelfSync": False,
                "continueReadingStatus": True,
                "metadataApiSync": True,
                "ratings": True,
                "bookmarks": True,
                "annotations": True,
                "firstSyncHashLookup": hash_lookup_configured,
                "metadataNetworkScopes": {
                    "readerPersonalMetadata": "public-authenticated",
                    "sharedBookMetadata": "lan-only",
                    "opfSidecarManagement": "lan-only",
                },
                "localOpfSidecarUi": True,
                "metadataImportConfigured": bool(settings.METADATA_IMPORT_ROOTS),
                "webReaderProjection": False,
            },
        })
    return _STATIC_CAPABILITIES
