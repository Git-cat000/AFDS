"""Bounded HTTP requests to explicitly configured model endpoints."""

import asyncio
import ipaddress
import json
import socket
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.errors import DomainError
from app.provider_settings import validate_url


class ProviderError(DomainError):
    def __init__(self, code, message, transient=False, rejected=False):
        super().__init__(code, message, 422)
        self.transient = transient
        self.rejected = rejected


def check_destination(url, allow_private=False, allow_query=False):
    try:
        parsed = urlsplit(url)
        validation_url = urlunsplit(parsed._replace(query="")) if allow_query else url
        validate_url(validation_url, allow_private)
        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        if not addresses or len(url) > 8192:
            raise ValueError("Invalid address")
        for entry in addresses:
            address = ipaddress.ip_address(entry[4][0])
            if address.is_link_local or address.is_multicast or address.is_unspecified:
                raise ValueError("Forbidden address")
            if not allow_private and not address.is_global:
                raise ValueError("Private address requires opt-in")
        addresses.sort(key=lambda entry: entry[0] != socket.AF_INET)
        return addresses[0][4][0]
    except (ValueError, OSError) as error:
        raise ProviderError(
            "PROVIDER_URL_BLOCKED",
            "Provider address is invalid, unreachable or disallowed",
            rejected=True,
        ) from error


class ProviderHTTP:
    def __init__(self, config):
        self.config = config

    def request(
        self,
        method,
        path,
        *,
        payload=None,
        multipart=None,
        output=None,
        external_url=None,
        checkpoint=lambda: True,
        limit=32 * 1024 * 1024,
    ):
        if not checkpoint():
            from app.media import Cancelled

            raise Cancelled()
        url = external_url or f"{self.config['base_url']}/{path.lstrip('/')}"
        private = self.config["allow_private_network"] if external_url is None else False
        address = check_destination(url, private, allow_query=external_url is not None)
        original = httpx.URL(url)
        pinned_url = original.copy_with(host=address)
        headers = {"Host": original.netloc.decode("ascii")}
        if external_url is None and self.config["api_key"]:
            headers["Authorization"] = f"Bearer {self.config['api_key']}"
        arguments = {
            "headers": headers,
            "extensions": {
                "sni_hostname": original.raw_host.decode("ascii"),
            },
        }
        if payload is not None:
            arguments["json"] = payload
        if multipart is not None:
            arguments["files"] = {key: (None, str(value)) for key, value in multipart.items()}

        async def perform():
            async with httpx.AsyncClient(
                timeout=self.config["timeout_seconds"], follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream(method, pinned_url, **arguments) as response:
                    status = response.status_code
                    if not 200 <= status < 300:
                        messages = {
                            401: ("PROVIDER_AUTH_FAILED", "API Key was rejected"),
                            403: ("PROVIDER_AUTH_FAILED", "Provider denied this request"),
                            404: (
                                "PROVIDER_ENDPOINT_UNSUPPORTED",
                                "Provider does not offer this API endpoint",
                            ),
                            429: ("PROVIDER_RATE_LIMIT", "Provider rate limit reached"),
                        }
                        code, message = messages.get(
                            status, ("PROVIDER_HTTP_ERROR", f"Provider returned HTTP {status}")
                        )
                        raise ProviderError(
                            code,
                            message,
                            transient=status == 429 or status >= 500,
                            rejected=400 <= status < 500 and status != 408,
                        )
                    chunks, size = [], 0
                    handle = output.open("wb") if output else None
                    try:
                        async for chunk in response.aiter_bytes():
                            if not checkpoint():
                                from app.media import Cancelled

                                raise Cancelled()
                            size += len(chunk)
                            if size > limit:
                                raise ProviderError(
                                    "PROVIDER_OUTPUT_TOO_LARGE",
                                    "Provider response exceeded size limit",
                                )
                            if handle:
                                handle.write(chunk)
                            else:
                                chunks.append(chunk)
                    finally:
                        if handle:
                            handle.close()
                    return b"".join(chunks)

        async def bounded():
            return await asyncio.wait_for(perform(), timeout=self.config["timeout_seconds"])

        try:
            return asyncio.run(bounded())
        except (httpx.TimeoutException, TimeoutError) as error:
            raise ProviderError(
                "PROVIDER_TIMEOUT", "Provider request timed out", transient=True
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise ProviderError(
                "PROVIDER_CONNECTION_FAILED",
                "Could not complete provider connection",
                transient=True,
            ) from error

    def json(self, method, path, **kwargs):
        data = self.request(method, path, **kwargs)
        try:
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("Expected object")
            return result
        except (ValueError, UnicodeDecodeError) as error:
            raise ProviderError(
                "PROVIDER_OUTPUT_INVALID", "Provider returned invalid JSON"
            ) from error

    def models(self):
        result = self.json("GET", "models", limit=4 * 1024 * 1024)
        entries = result.get("data")
        if not isinstance(entries, list):
            raise ProviderError("PROVIDER_OUTPUT_INVALID", "Model discovery expected a data list")
        secret = self.config["api_key"]
        models = sorted(
            {
                entry["id"]
                for entry in entries
                if isinstance(entry, dict)
                and isinstance(entry.get("id"), str)
                and len(entry["id"]) <= 200
                and (not secret or secret not in entry["id"])
            }
        )[:500]
        return {
            "status": "connected",
            "models": models,
            "message": "连接与鉴权通过。生成能力需运行任务验证。",
            "generation_tested": False,
        }
