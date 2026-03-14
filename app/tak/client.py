"""TLS client for sending Cursor on Target payloads to TAK."""

import asyncio
from configparser import ConfigParser, SectionProxy

import pytak

from app.settings import Settings


class TakSendError(Exception):
    """Raised when a TAK send attempt fails."""


class TakTlsClient:
    """Serialize CoT delivery over a TLS connection to the TAK server."""

    def __init__(self, settings: Settings) -> None:
        """Build the PyTAK-backed client and its transport configuration.

        Connection reuse is coordinated by a higher-level delivery worker, so
        this client only manages the socket lifecycle for one persistent TLS
        session at a time.
        """
        self.settings = settings
        self.pytak_config = self._build_pytak_config()
        self._writer: asyncio.StreamWriter | None = None
        self._tx_worker: pytak.TXWorker | None = None

    def _build_pytak_config(self) -> SectionProxy:
        """Create the PyTAK configuration used for TLS transport setup.
        """
        config = ConfigParser()
        config.add_section("pytak")
        config.set(
            "pytak",
            "COT_URL",
            f"tls://{self.settings.tak_host}:{self.settings.tak_port}",
        )
        config.set(
            "pytak",
            "PYTAK_TLS_CLIENT_CERT",
            str(self.settings.tak_client_cert_file),
        )
        config.set(
            "pytak",
            "PYTAK_TLS_CLIENT_KEY",
            str(self.settings.tak_client_key_file),
        )
        config.set(
            "pytak",
            "PYTAK_TLS_CLIENT_CAFILE",
            str(self.settings.tak_ca_file),
        )
        config.set(
            "pytak",
            "PYTAK_TLS_SERVER_EXPECTED_HOSTNAME",
            self.settings.tak_server_hostname,
        )
        config.set("pytak", "PYTAK_TLS_DONT_VERIFY", "0")
        config.set("pytak", "PYTAK_TLS_DONT_CHECK_HOSTNAME", "0")
        if self.settings.tak_client_key_password:
            config.set(
                "pytak",
                "PYTAK_TLS_CLIENT_PASSWORD",
                self.settings.tak_client_key_password,
            )
        return config["pytak"]

    async def connect(self) -> None:
        """Open the persistent TLS connection if it is not already open.

        Connection setup is guarded by an explicit timeout so the coordinator
        can fail fast and reconnect on the next queued delivery.
        """
        if self._writer is not None and not self._writer.is_closing():
            return

        try:
            _, writer = await asyncio.wait_for(
                pytak.protocol_factory(self.pytak_config),
                timeout=self.settings.tak_connect_timeout_sec,
            )
        except Exception as exc:
            raise TakSendError(str(exc)) from exc

        self._writer = writer
        self._tx_worker = pytak.TXWorker(asyncio.Queue(), self.pytak_config, writer)

    async def send_on_existing_connection(self, payload: bytes) -> None:
        """Write one payload to the already-open TLS connection.

        The caller is responsible for ensuring ``connect()`` succeeded first.
        Any transport failure is wrapped so higher layers can handle delivery
        errors uniformly.
        """
        writer = self._writer
        tx_worker = self._tx_worker
        if writer is None or writer.is_closing() or tx_worker is None:
            raise TakSendError("TAK connection is not established")

        try:
            await asyncio.wait_for(
                tx_worker.send_data(payload),
                timeout=self.settings.tak_write_timeout_sec,
            )
        except Exception as exc:
            raise TakSendError(str(exc)) from exc

    async def close(self) -> None:
        """Close the persistent TLS connection if it is open."""
        writer = self._writer
        self._writer = None
        self._tx_worker = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
