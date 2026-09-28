"""Resolve a request's credentials to a same-user Official Bearer token."""

from fastapi import HTTPException, status

from grimmlink_adapter.official.exceptions import OfficialAuthError, OfficialClientError
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.auth_service import AuthService
from grimmlink_adapter.services.linked_auth import LinkedAuthUnavailable, get_linked_bearer


class VerifiedBearer(str):
    """Bearer string carrying the user ID verified by Official auth."""

    user_id: int

    def __new__(cls, value: str, user_id: int) -> "VerifiedBearer":
        instance = str.__new__(cls, value)
        instance.user_id = user_id
        return instance


async def get_official_bearer(creds: ClientCredentials) -> VerifiedBearer:
    """Verify incoming identities and use a linked JWT for MD5-only requests."""
    from grimmlink_adapter.config import settings

    auth = AuthService()
    server = settings.GRIMMORY_BASE_URL.rstrip("/")
    try:
        if creds.bearer_token:
            bearer_identity = await auth.authorize_client(
                ClientCredentials(bearer_token=creds.bearer_token)
            )
            if creds.username and creds.md5_key:
                md5_identity = await auth.authorize_client(
                    ClientCredentials(username=creds.username, md5_key=creds.md5_key)
                )
                if md5_identity.userId != bearer_identity.userId:
                    raise HTTPException(status_code=401, detail="Credential identities do not match.")
            return VerifiedBearer(creds.bearer_token, bearer_identity.userId)

        identity = await auth.authorize_client(creds)
        if not creds.username or not creds.md5_key:
            raise HTTPException(status_code=401, detail="Authentication credentials are required.")
        token = await get_linked_bearer(server, str(identity.userId), creds.username, creds.md5_key)
        return VerifiedBearer(token, identity.userId)
    except HTTPException:
        raise
    except LinkedAuthUnavailable as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except OfficialAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Upstream authentication failed.") from exc
    except OfficialClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Upstream authentication is unavailable.") from exc
    finally:
        # This helper owns the per-request AuthService/client it creates.
        client = getattr(auth, "official_client", None)
        if client is not None:
            await client.aclose()
