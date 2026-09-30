"""Narrow compatibility policies for external MySQL connections."""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine as sqlalchemy_create_async_engine
from sqlalchemy import create_engine as sqlalchemy_create_engine

logger = logging.getLogger(__name__)

SSL_DISABLED_ENDPOINTS_ENV = "MYSQL_SSL_DISABLED_ENDPOINTS"


def _normalize_endpoint(host: str, port: int, database: str) -> tuple[str, int, str]:
    return host.strip().lower(), int(port), database.strip()


def _configured_ssl_disabled_endpoints() -> set[tuple[str, int, str]]:
    endpoints: set[tuple[str, int, str]] = set()
    for raw_endpoint in os.getenv(SSL_DISABLED_ENDPOINTS_ENV, "").split(","):
        raw_endpoint = raw_endpoint.strip()
        if not raw_endpoint:
            continue
        try:
            host_port, database = raw_endpoint.rsplit("/", 1)
            host, raw_port = host_port.rsplit(":", 1)
            endpoint = _normalize_endpoint(host, int(raw_port), database)
        except (TypeError, ValueError):
            logger.warning(
                "Ignoring invalid %s entry: %s",
                SSL_DISABLED_ENDPOINTS_ENV,
                raw_endpoint,
            )
            continue
        if all((endpoint[0], endpoint[2])) and 0 < endpoint[1] <= 65535:
            endpoints.add(endpoint)
    return endpoints


def should_disable_mysql_ssl(host: str, port: int, database: str) -> bool:
    """Return true only for an explicitly configured host/port/database tuple."""
    endpoint = _normalize_endpoint(host, port, database)
    disabled = endpoint in _configured_ssl_disabled_endpoints()
    if disabled:
        logger.warning(
            "MySQL TLS disabled by scoped compatibility policy for %s:%s/%s",
            endpoint[0],
            endpoint[1],
            endpoint[2],
        )
    return disabled


def mysql_connect_args(
    host: str,
    port: int,
    database: str,
    *,
    base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build PyMySQL connect args without weakening unrelated connections."""
    result = dict(base or {})
    if should_disable_mysql_ssl(host, port, database):
        result["ssl_disabled"] = True
    return result


def mysql_connect_args_for_url(
    connection_url: str,
    *,
    base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build scoped connect args from a SQLAlchemy MySQL URL."""
    url = make_url(connection_url)
    return mysql_connect_args(
        url.host or "",
        url.port or 3306,
        url.database or "",
        base=base,
    )


def create_mysql_engine(connection_url: str, **kwargs: Any):
    """Create a synchronous engine with the scoped compatibility policy."""
    base = kwargs.pop("connect_args", None)
    return sqlalchemy_create_engine(
        connection_url,
        connect_args=mysql_connect_args_for_url(connection_url, base=base),
        **kwargs,
    )


def create_async_mysql_engine(connection_url: str, **kwargs: Any):
    """Create an async engine; aiomysql uses ssl=None to disable TLS explicitly."""
    base = dict(kwargs.pop("connect_args", None) or {})
    url = make_url(connection_url)
    if should_disable_mysql_ssl(url.host or "", url.port or 3306, url.database or ""):
        base["ssl"] = None
    return sqlalchemy_create_async_engine(connection_url, connect_args=base, **kwargs)
