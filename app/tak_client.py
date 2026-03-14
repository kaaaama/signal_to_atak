"""TLS client for sending Cursor on Target payloads to TAK."""

import asyncio
import ssl

from app.settings import Settings


class TakSendError(Exception):
    """Raised when a TAK send attempt fails."""


class TakTlsClient:
    """Serialize CoT delivery over a TLS connection to the TAK server."""

    def __init__(self, settings: Settings) -> None:
        """Build the TLS client and its reusable SSL context.

        Connection reuse is coordinated by a higher-level delivery worker, so
        this client only manages the socket lifecycle for one persistent TLS
        session at a time.
        """
        self.settings = settings
        self.ssl_context = self._build_ssl_context()
        self._writer: asyncio.StreamWriter | None = None

    def _build_ssl_context(self) -> ssl.SSLContext:
        """Create an SSL context configured for mutual TLS with TAK.

        The context enforces certificate validation, hostname checking, TLS 1.2+
        and loads the configured CA, client certificate, and client key.
        """
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cafile=str(self.settings.tak_ca_file))
        ctx.load_cert_chain(
            certfile=str(self.settings.tak_client_cert_file),
            keyfile=str(self.settings.tak_client_key_file),
            password=self.settings.tak_client_key_password,
        )
        return ctx

    async def connect(self) -> None:
        """Open the persistent TLS connection if it is not already open.

        Connection setup is guarded by an explicit timeout so the coordinator
        can fail fast and reconnect on the next queued delivery.
        """
        if self._writer is not None and not self._writer.is_closing():
            return

        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=self.settings.tak_host,
                    port=self.settings.tak_port,
                    ssl=self.ssl_context,
                    server_hostname=self.settings.tak_server_hostname,
                ),
                timeout=self.settings.tak_connect_timeout_sec,
            )
        except Exception as exc:
            raise TakSendError(str(exc)) from exc

        self._writer = writer

    async def send_on_existing_connection(self, payload: bytes) -> None:
        """Write one payload to the already-open TLS connection.

        The caller is responsible for ensuring ``connect()`` succeeded first.
        Any transport failure is wrapped so higher layers can handle delivery
        errors uniformly.
        """
        writer = self._writer
        if writer is None or writer.is_closing():
            raise TakSendError("TAK connection is not established")

        try:
            writer.write(payload)
            await asyncio.wait_for(
                writer.drain(),
                timeout=self.settings.tak_write_timeout_sec,
            )
        except Exception as exc:
            raise TakSendError(str(exc)) from exc

    async def close(self) -> None:
        """Close the persistent TLS connection if it is open."""
        writer = self._writer
        self._writer = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
