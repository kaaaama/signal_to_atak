"""TLS client for sending Cursor on Target payloads to TAK."""

import asyncio
import ssl

from app.settings import Settings


class TakSendError(Exception):
    """Raised when a TAK send attempt fails."""

    pass


class TakTlsClient:
    """Serialize CoT delivery over a TLS connection to the TAK server."""

    def __init__(self, settings: Settings) -> None:
        """Build the TLS client and its reusable SSL context.

        A single async lock is also created here so overlapping send attempts
        from background tasks do not interleave writes on separate sockets in an
        uncontrolled way.
        """
        self.settings = settings
        self.ssl_context = self._build_ssl_context()
        self._send_lock = asyncio.Lock()

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

    async def send_event(self, payload: bytes) -> None:
        """Open a TLS connection, send one CoT payload, and close the socket.

        A fresh connection is opened for each payload. Connection setup and
        socket drain are both guarded by explicit timeouts, and any underlying
        exception is wrapped in ``TakSendError`` so callers can treat transport
        failures uniformly.
        """
        async with self._send_lock:
            writer: asyncio.StreamWriter | None = None

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

                writer.write(payload)
                await asyncio.wait_for(
                    writer.drain(),
                    timeout=self.settings.tak_write_timeout_sec,
                )

            except Exception as exc:
                raise TakSendError(str(exc)) from exc

            finally:
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
