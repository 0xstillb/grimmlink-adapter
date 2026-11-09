"""Advertise the local Adapter UI through multicast DNS."""

from __future__ import annotations

import logging
import socket

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from grimmlink_adapter.config import settings

logger = logging.getLogger(__name__)


def _lan_ipv4() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        raw_address = probe.getsockname()[0]
    finally:
        probe.close()
    if not isinstance(raw_address, str):
        raise OSError("Could not determine the local IPv4 address for mDNS")
    address = raw_address
    if not address or address.startswith("127."):
        raise OSError("No LAN IPv4 address is available for mDNS")
    return address


class MDNSAdvertiser:
    def __init__(self) -> None:
        self._zeroconf: AsyncZeroconf | None = None
        self._service: ServiceInfo | None = None
        self._host: str | None = None
        self._address: str | None = None
        self._running = False
        self._error_code: str | None = None

    def status(self) -> dict[str, object]:
        """Return non-secret mDNS state for local diagnostics."""
        return {
            "enabled": settings.MDNS_ENABLED,
            "running": self._running,
            "inspected": True,
            "host": self._host,
            "address": self._address,
            "port": settings.ADAPTER_PORT,
            "errorCode": self._error_code,
        }

    async def start(self) -> None:
        if not settings.MDNS_ENABLED:
            return
        host = settings.MDNS_HOSTNAME.rstrip(".")
        if not host.lower().endswith(".local"):
            host = f"{host}.local"
        service_instance = f"{settings.MDNS_SERVICE_NAME}._http._tcp.local."
        try:
            # In Docker bridge mode the container's route address is not
            # reachable from the LAN. Deployments can publish the host's LAN
            # address explicitly; native/host networking can auto-detect it.
            self._address = settings.MDNS_IPV4 or _lan_ipv4()
            address = socket.inet_aton(self._address)
            self._host = host
            self._zeroconf = AsyncZeroconf()
            self._service = ServiceInfo(
                type_="_http._tcp.local.",
                name=service_instance,
                port=settings.ADAPTER_PORT,
                addresses=[address],
                server=f"{host}.",
                properties={"path": "/", "app": "GrimmLink Adapter"},
            )
            await self._zeroconf.async_register_service(self._service, allow_name_change=False)
            self._running = True
            self._error_code = None
            logger.info("Advertised local UI at http://%s:%d", host, settings.ADAPTER_PORT)
        except Exception as exc:
            self._running = False
            self._error_code = type(exc).__name__
            logger.warning("mDNS advertisement is unavailable: %s", type(exc).__name__)
            await self.stop()

    async def stop(self) -> None:
        if self._zeroconf is None:
            self._running = False
            return
        try:
            if self._service is not None:
                await self._zeroconf.async_unregister_service(self._service)
        finally:
            await self._zeroconf.async_close()
            self._service = None
            self._zeroconf = None
            self._running = False
