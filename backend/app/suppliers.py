"""Supplier lookup: Claude with web search finds current Irish and UK listings for one part.

Follows the bundled Claude API guidance (official ``anthropic`` SDK; model ``claude-opus-5-5``
by default; adaptive thinking with an explicit effort ``medium``; server-side fallbacks
``betas=["server-side-fallback-2026-07-01"]`` + ``fallbacks="default"``; ``stop_reason``
checked before any content is read; typed SDK errors caught most-specific first):

* Tools: the server-side ``web_search_20260209`` (``user_location`` Ireland, at most
  :data:`MAX_SEARCHES` searches) and ``web_fetch_20260209`` (at most :data:`MAX_FETCHES`
  pages). They run on Anthropic's servers inside one request; a long search loop ends with
  ``stop_reason: "pause_turn"`` and the paused turn is sent back unchanged (after
  ``sanitize_for_echo``) to resume, at most :data:`MAX_PAUSE_CONTINUES` times.
* Search and fetch errors do not raise: they arrive as ``web_search_tool_result`` /
  ``web_fetch_tool_result`` blocks whose ``content`` is an error object (``error_code``).
  They are collected and reported when no listing comes back.
* The answer is structured JSON (``output_config.format`` with :data:`ANSWER_SCHEMA`), validated
  again here with Pydantic. If the API refuses the format together with the web tools (HTTP
  400), the request is retried once without it and the JSON is read from the final text block
  (``SupplierAnswer`` validation is the same either way).

Then every URL is checked from the server (:func:`check_url`: ``HEAD``, then ``GET`` when the
shop refuses ``HEAD``; status 200-399 within :data:`URL_TIMEOUT_S` is working; redirects are not
followed, so a 3xx counts as working without the server being sent elsewhere; hosts that are IP
literals or resolve to private, loopback or link-local addresses are never contacted).
Listings are upserted by URL (or by shop and country) with ``last_checked_at`` = now; listings
Claude did not return are kept and become stale after 30 days (the parts list says so).

Test seam: outside production, ``CLAUDE_FAKE_SUPPLIER_FILE`` replaces the API call. The file
holds an answer object, an envelope ``{"stop_reason": "end_turn", "output": {...}}`` or
``{"stop_reason": "refusal", "stop_details": {"category": "cyber"}}``, or a map
``{"by_part": {"<manufacturer> <model>": answer, ...}, "default": answer}``.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import anthropic
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.assistant.chat import sanitize_for_echo
from app.config import Settings
from app.db import utcnow
from app.models import Part, PartListing

log = logging.getLogger("app.suppliers")

MISSING_KEY_MESSAGE = (
    "Add the ANTHROPIC_API_KEY secret in GitHub and redeploy to enable supplier lookups"
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORT = "medium"
MAX_TOKENS = 16000
REQUEST_TIMEOUT_S = 240.0
MAX_PAUSE_CONTINUES = 4
MAX_SEARCHES = 6
MAX_FETCHES = 6
MAX_LISTINGS = 10
RATE_LIMIT = timedelta(hours=1)
URL_TIMEOUT_S = 6.0
URL_CHECK_WORKERS = 4
USER_AGENT = "Mozilla/5.0 (compatible; VTOL-Drone-Designer link check)"

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "listings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "supplier_name": {"type": "string"},
                    "country": {"type": "string", "enum": ["IE", "UK"]},
                    "url": {"type": "string"},
                    "price_eur": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                    "price_as_shown": {"type": "string"},
                    "in_stock": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
                    "match": {"type": "string", "enum": ["exact", "variant", "uncertain"]},
                    "note": {"type": "string"},
                },
                "required": [
                    "supplier_name",
                    "country",
                    "url",
                    "price_eur",
                    "price_as_shown",
                    "in_stock",
                    "match",
                    "note",
                ],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["listings", "summary"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You find where an owner in Ireland can buy one specific drone part today. Search Irish and UK \
online shops for exactly the manufacturer and model given (the same variant: KV, size, cell \
count, pitch, connector) and report each shop's product page.

Rules:
- Only shops based in Ireland (country "IE") or the United Kingdom ("UK"). Ignore marketplaces \
(eBay, Amazon, AliExpress) and shops elsewhere.
- url must be the product page of that exact item, copied from a search result or a page you \
fetched; never construct or guess a URL.
- price_eur: the price in euro including VAT. Convert pounds at 1 GBP = 1.17 EUR; if a UK shop \
shows prices excluding VAT, add 20 % first. price_as_shown repeats the price as the page shows \
it (for example "GBP 96.90 inc VAT"). Use null when no price is visible.
- in_stock: true or false only when the page says so; otherwise null.
- match: "exact" for exactly this part, "variant" for another KV, size or pack of the same \
product line, "uncertain" when you cannot tell. Only exact matches are used.
- note: one short plain sentence (for example "sold as a pair", "pre-order").
- At most ten listings. An empty list is a valid answer when nothing is found; say so in the \
summary.
"""


# ---------------------------------------------------------------------------
# Validation of Claude's answer
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class ListingAnswer(_Strict):
    supplier_name: str = Field(min_length=1, max_length=200)
    country: Literal["IE", "UK"]
    url: str = Field(max_length=2000)
    price_eur: float | None = Field(None, ge=0, le=100_000)
    price_as_shown: str = Field("", max_length=200)
    in_stock: bool | None = None
    match: Literal["exact", "variant", "uncertain"]
    note: str = Field("", max_length=1000)

    @field_validator("supplier_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("blank supplier name")
        return value

    @field_validator("url")
    @classmethod
    def _http(cls, value: str) -> str:
        value = value.strip()
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("not an http(s) URL")
        return value


class SupplierAnswer(_Strict):
    listings: list[ListingAnswer] = Field(default_factory=list, max_length=25)
    summary: str = Field("", max_length=4000)


# ---------------------------------------------------------------------------
# Results and errors
# ---------------------------------------------------------------------------


class SupplierError(Exception):
    """The lookup failed. ``str(exc)`` is a plain message for the owner."""


class SupplierNotConfigured(SupplierError):
    """No API key and no test seam."""


@dataclass
class LookupResult:
    status: Literal["ok", "refused"]
    model: str
    answer: SupplierAnswer | None = None
    refusal_category: str | None = None
    search_errors: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


def _api_key(settings: Settings) -> str | None:
    if settings.anthropic_api_key is None:
        return None
    key = settings.anthropic_api_key.get_secret_value().strip()
    return key or None


def is_available(settings: Settings) -> bool:
    return settings.fake_claude_supplier_file is not None or _api_key(settings) is not None


def _validate(payload: Any) -> SupplierAnswer:
    if isinstance(payload, dict) and isinstance(payload.get("listings"), list):
        # Drop malformed items one by one instead of failing the whole answer.
        good = []
        for item in payload["listings"]:
            try:
                good.append(ListingAnswer.model_validate(item))
            except ValidationError:
                log.info("Dropped a malformed listing from Claude's answer")
        payload = {**payload, "listings": [g.model_dump() for g in good]}
    try:
        return SupplierAnswer.model_validate(payload)
    except ValidationError:
        raise SupplierError("Claude's answer could not be read. Try again later.") from None


# ---------------------------------------------------------------------------
# The Claude call
# ---------------------------------------------------------------------------


def _prompt(part: Part) -> str:
    spec = part.spec or {}
    key_specs = {
        k: spec[k]
        for k in (
            "kv_rpm_per_v",
            "stator_size",
            "diameter_mm",
            "pitch_mm",
            "trade_size",
            "continuous_current_a",
            "cells_series",
            "capacity_mah",
            "discharge_c_continuous",
            "connector",
            "torque_kg_cm",
            "frequency_mhz",
            "outer_diameter_mm",
            "inner_diameter_mm",
            "length_mm",
            "format",
        )
        if k in spec
    }
    known = [f"- {li.supplier_name} ({li.country}): {li.url}" for li in part.listings]
    lines = [
        f"Part: {part.manufacturer} {part.model} (category: {part.category}).",
        f"Key specifications: {json.dumps(key_specs, sort_keys=True)}.",
        f"Manufacturer page: {part.source or 'unknown'}.",
    ]
    if known:
        lines.append("Listings found before (check they are still current, and look for more):")
        lines.extend(known)
    lines.append("Find current price, stock and the product page at Irish and UK shops.")
    return "\n".join(lines)


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": MAX_SEARCHES,
            "user_location": {
                "type": "approximate",
                "country": "IE",
                "city": "Dublin",
                "timezone": "Europe/Dublin",
            },
        },
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": MAX_FETCHES},
    ]


def _send(client: anthropic.Anthropic, **kwargs: Any) -> Any:
    """The one place the API is called (tests replace it)."""
    return client.beta.messages.create(**kwargs)


def _block_dicts(message: Any) -> list[dict[str, Any]]:
    out = []
    for block in getattr(message, "content", None) or []:
        if isinstance(block, dict):
            out.append(block)
        elif hasattr(block, "to_dict"):
            out.append(block.to_dict())
        else:
            out.append(dict(block))
    return out


def search_errors(content: list[dict[str, Any]]) -> list[str]:
    """Error codes from server-tool result blocks (errors are results, not exceptions: a
    success ``content`` is a list, an error ``content`` is an object with ``error_code``)."""
    errors = []
    for block in content:
        if block.get("type") in ("web_search_tool_result", "web_fetch_tool_result"):
            body = block.get("content")
            if isinstance(body, dict) and body.get("error_code"):
                errors.append(f"{block['type'].split('_')[1]}: {body['error_code']}")
    return errors


def _final_text(content: list[dict[str, Any]]) -> str | None:
    texts = [b.get("text", "") for b in content if b.get("type") == "text"]
    return "".join(texts) if texts else None


def _parse_json_text(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except ValueError:
            pass
    raise SupplierError("Claude's answer could not be read. Try again later.")


def _usage(message: Any, total: dict[str, Any]) -> None:
    usage = getattr(message, "usage", None)
    for key in ("input_tokens", "output_tokens"):
        total[key] = (total.get(key) or 0) + (getattr(usage, key, None) or 0)
    server = getattr(usage, "server_tool_use", None)
    if server is not None:
        total["web_search_requests"] = (total.get("web_search_requests") or 0) + (
            getattr(server, "web_search_requests", None) or 0
        )
    iterations = getattr(usage, "iterations", None) or []
    if any(getattr(e, "type", None) == "fallback_message" for e in iterations):
        total["served_by_fallback"] = True


def _converse(
    client: anthropic.Anthropic, model: str, part: Part, structured: bool
) -> LookupResult:
    user = {"role": "user", "content": _prompt(part)}
    messages: list[dict[str, Any]] = [user]
    output_config: dict[str, Any] = {"effort": EFFORT}
    system = SYSTEM_PROMPT
    if structured:
        output_config["format"] = {"type": "json_schema", "schema": ANSWER_SCHEMA}
    else:
        system += (
            "\nEnd your answer with one JSON object and nothing after it, matching: "
            + json.dumps(ANSWER_SCHEMA, sort_keys=True)
        )
    usage: dict[str, Any] = {}
    errors: list[str] = []
    for _attempt in range(MAX_PAUSE_CONTINUES + 1):
        message = _send(
            client,
            model=model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config=output_config,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=system,
            tools=_tools(),
            messages=messages,
        )
        _usage(message, usage)
        content = sanitize_for_echo(_block_dicts(message))
        errors.extend(search_errors(content))
        served_by = getattr(message, "model", None) or model
        stop_reason = getattr(message, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            return LookupResult(
                "refused",
                served_by,
                refusal_category=getattr(details, "category", None),
                search_errors=errors,
                usage=usage,
            )
        if stop_reason == "pause_turn":
            # The server-side search loop paused: send the turn back to resume it (no extra
            # user message; the API sees the trailing server tool use and continues).
            messages = [user, {"role": "assistant", "content": content}]
            continue
        if stop_reason == "max_tokens":
            raise SupplierError("Claude's answer was cut off. Try again later.")
        if stop_reason != "end_turn":
            log.warning("Unexpected stop_reason from Claude: %s", stop_reason)
            raise SupplierError("Claude stopped before finishing its answer. Try again later.")
        text = _final_text(content)
        if text is None:
            raise SupplierError(
                "Claude did not return an answer"
                + (f" (search problems: {', '.join(errors)})" if errors else "")
                + ". Try again later."
            )
        answer = _validate(_parse_json_text(text))
        return LookupResult("ok", served_by, answer=answer, search_errors=errors, usage=usage)
    raise SupplierError("The web search took too long. Try again later.")


def _fake(path: Path, model: str, part: Part) -> LookupResult:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SupplierError("The fake supplier response file could not be read.") from None
    usage = {"input_tokens": 0, "output_tokens": 0, "fake": True}
    if isinstance(payload, dict) and "by_part" in payload:
        name = f"{part.manufacturer} {part.model}"
        payload = payload["by_part"].get(name, payload.get("default"))
        if payload is None:
            payload = {"listings": [], "summary": "No listings found (fake)."}
    errors: list[str] = []
    if isinstance(payload, dict) and "stop_reason" in payload:
        errors = list(payload.get("search_errors") or [])
        if payload["stop_reason"] == "refusal":
            details = payload.get("stop_details") or {}
            return LookupResult(
                "refused", model, refusal_category=details.get("category"), usage=usage
            )
        payload = payload.get("output")
    return LookupResult("ok", model, answer=_validate(payload), search_errors=errors, usage=usage)


def lookup(settings: Settings, part: Part) -> LookupResult:
    """Ask Claude (with web search) for current Irish and UK listings of ``part``.

    Raises :class:`SupplierNotConfigured` without a key, :class:`SupplierError` with a plain
    message for every other failure. A refusal is a result, not an error."""
    model = settings.claude_model
    fake = settings.fake_claude_supplier_file
    if fake is not None:
        return _fake(fake, model, part)
    key = _api_key(settings)
    if key is None:
        raise SupplierNotConfigured(MISSING_KEY_MESSAGE)
    client = anthropic.Anthropic(api_key=key, timeout=REQUEST_TIMEOUT_S, max_retries=2)
    try:
        try:
            return _converse(client, model, part, structured=True)
        except anthropic.BadRequestError as exc:
            # Structured output and the web tools' citations may not combine; retry once with
            # the JSON asked for in the prompt (validated the same way).
            log.warning(
                "Structured supplier request rejected (request_id=%s); retrying without the "
                "output format",
                exc.request_id,
            )
            return _converse(client, model, part, structured=False)
    except anthropic.AuthenticationError:
        raise SupplierError(
            "Claude did not accept the API key. Check the ANTHROPIC_API_KEY secret and redeploy."
        ) from None
    except anthropic.PermissionDeniedError:
        raise SupplierError(
            "The Claude API key is not allowed to use this model or web search. Check the "
            "key's workspace and its web search setting."
        ) from None
    except anthropic.NotFoundError:
        raise SupplierError(
            f"The Claude model '{model}' is not available to this API key."
        ) from None
    except anthropic.BadRequestError as exc:
        log.warning("Claude rejected the supplier request: request_id=%s", exc.request_id)
        raise SupplierError("Claude could not run this search. Try again later.") from None
    except anthropic.RateLimitError:
        raise SupplierError(
            "Claude is handling too many requests right now. Wait a minute and try again."
        ) from None
    except anthropic.APIStatusError as exc:
        log.warning("Claude API error: status=%s request_id=%s", exc.status_code, exc.request_id)
        if exc.status_code >= 500:
            raise SupplierError(
                "Claude is temporarily unavailable. Try again in a few minutes."
            ) from None
        raise SupplierError(f"Claude returned an error (HTTP {exc.status_code}).") from None
    except anthropic.APITimeoutError:
        raise SupplierError("Claude took too long to answer. Try again later.") from None
    except anthropic.APIConnectionError:
        raise SupplierError(
            "The server could not reach Claude. Try again in a few minutes."
        ) from None


# ---------------------------------------------------------------------------
# URL checks
# ---------------------------------------------------------------------------


def _public_host(host: str) -> bool:
    """False for IP literals, localhost-style names and hosts that resolve to private,
    loopback, link-local or reserved addresses (no server-side request forgery)."""
    host = host.strip("[]").lower()
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        ipaddress.ip_address(host)
        return False  # IP literals are never shop links
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # unresolvable here (for example behind a proxy): let the request decide
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return False
    return True


def check_url(url: str) -> tuple[bool, int | None]:
    """(working, HTTP status or None): HEAD, then GET when HEAD is refused; 200-399 works."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False, None
    if not _public_host(parts.hostname):
        return False, None
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,*/*"}
    try:
        with httpx.Client(timeout=URL_TIMEOUT_S, follow_redirects=False, headers=headers) as c:
            response = c.head(url)
            status = response.status_code
            if status in (400, 403, 404, 405, 429, 501) or status >= 500:
                with c.stream("GET", url) as got:
                    status = got.status_code
    except httpx.HTTPError:
        return False, None
    return 200 <= status <= 399, status


# ---------------------------------------------------------------------------
# Refresh one part
# ---------------------------------------------------------------------------


def rate_limited_until(part: Part, now: datetime | None = None) -> datetime | None:
    """When the next refresh is allowed, or None if it is allowed now."""
    if part.listings_refreshed_at is None:
        return None
    until = part.listings_refreshed_at + RATE_LIMIT
    return until if (now or utcnow()) < until else None


def mark_queued(db: Session, part: Part) -> None:
    part.listings_refresh_status = "queued"
    part.listings_refresh_message = "Waiting for the worker."
    part.listings_refreshed_at = utcnow()


def refresh_part(
    session_factory: sessionmaker[Session],
    settings: Settings,
    part_id: int,
    url_checker: Callable[[str], tuple[bool, int | None]] | None = None,
) -> dict[str, Any]:
    """Look up, check and store the listings of one part. Returns a summary
    ``{status, message, added, updated, checked, working, ignored}``; raises
    :class:`SupplierError` (stored on the part as well) when the lookup fails."""
    checker = url_checker or check_url
    with session_factory() as db:
        part = db.scalar(
            select(Part).where(Part.id == part_id).options(selectinload(Part.listings))
        )
        if part is None:
            raise SupplierError("That part no longer exists.")
        part.listings_refresh_status = "running"
        part.listings_refresh_message = "Searching Irish and UK shops."
        part.listings_refreshed_at = utcnow()
        db.commit()
        db.refresh(part)
        try:
            result = lookup(settings, part)
        except SupplierError as exc:
            part.listings_refresh_status = "error"
            part.listings_refresh_message = str(exc)
            db.commit()
            raise
        if result.status == "refused":
            part.listings_refresh_status = "refused"
            part.listings_refresh_message = (
                "Claude declined this search"
                + (f" ({result.refusal_category})" if result.refusal_category else "")
                + ". The listings were not changed."
            )
            db.commit()
            return {
                "status": "refused",
                "message": part.listings_refresh_message,
                "added": 0,
                "updated": 0,
                "checked": 0,
                "working": 0,
                "ignored": 0,
            }
        assert result.answer is not None
        exact = [li for li in result.answer.listings if li.match == "exact"][:MAX_LISTINGS]
        ignored = len(result.answer.listings) - len(exact)
        with ThreadPoolExecutor(max_workers=URL_CHECK_WORKERS) as pool:
            checks = list(pool.map(lambda li: checker(li.url), exact))
        now = utcnow()
        added = updated = working = 0
        existing = list(part.listings)
        for li, (ok, status) in zip(exact, checks, strict=True):
            row = next((e for e in existing if e.url == li.url), None) or next(
                (
                    e
                    for e in existing
                    if e.supplier_name.lower() == li.supplier_name.lower()
                    and e.country == li.country
                ),
                None,
            )
            if row is None:
                row = PartListing(part_id=part.id, supplier_name=li.supplier_name, url=li.url)
                db.add(row)
                existing.append(row)
                added += 1
            else:
                updated += 1
            row.url = li.url
            row.supplier_name = li.supplier_name
            row.country = li.country
            row.price_eur = round(li.price_eur, 2) if li.price_eur is not None else None
            row.in_stock = li.in_stock
            row.last_checked_at = now
            row.url_ok = ok
            row.url_status = status
            row.url_checked_at = now
            working += 1 if ok else 0
        message = (
            f"Found {len(exact)} listing(s) ({added} new, {updated} updated); {working} link(s) "
            "answered."
        )
        if ignored:
            message += f" {ignored} result(s) were other variants and were ignored."
        if not exact:
            message = "No current Irish or UK listing was found; the existing listings are kept."
            if result.search_errors:
                message += " Search problems: " + ", ".join(sorted(set(result.search_errors))) + "."
        part.listings_refresh_status = "done"
        part.listings_refresh_message = message[:2000]
        db.commit()
        log.info("Refreshed listings of part %s: %s (usage %s)", part_id, message, result.usage)
        return {
            "status": "done",
            "message": message,
            "added": added,
            "updated": updated,
            "checked": len(exact),
            "working": working,
            "ignored": ignored,
            "model": result.model,
            "summary": result.answer.summary,
        }
