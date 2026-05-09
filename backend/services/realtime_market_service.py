from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
import websockets

from backend.core.config import settings

logger = logging.getLogger("stock_signals.realtime")

SUPPORTED_CHANNELS = ("trades", "quotes")


def parse_stream_tickers(raw_tickers: str | list[str] | None) -> list[str]:
    if isinstance(raw_tickers, str):
        values = raw_tickers.split(",")
    else:
        values = raw_tickers or []

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip().upper()
        if not cleaned or cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)
        if len(normalized) >= settings.realtime_max_tickers:
            break
    return normalized


def parse_stream_channels(raw_channels: str | list[str] | None) -> list[str]:
    if isinstance(raw_channels, str):
        values = raw_channels.split(",")
    else:
        values = raw_channels or []

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip().lower()
        if cleaned in SUPPORTED_CHANNELS and cleaned not in seen:
            normalized.append(cleaned)
            seen.add(cleaned)
    return normalized or list(SUPPORTED_CHANNELS)


def get_realtime_capabilities(*, realtime_entitled: bool = True) -> dict[str, Any]:
    configured = bool(settings.alpaca_api_key_id and settings.alpaca_api_secret_key)
    true_tick_supported = (
        settings.realtime_stream_enabled
        and settings.market_data_provider == "alpaca"
        and configured
        and realtime_entitled
    )
    internal_polling_supported = _internal_owned_polling_supported(realtime_entitled=realtime_entitled)
    provider_label = settings.market_data_provider
    feed = settings.alpaca_stock_feed
    if provider_label == "alpaca" and not configured:
        provider_label = "alpaca-unconfigured"
    if internal_polling_supported and not true_tick_supported:
        provider_label = "internal_owned_api"
        configured = True
        feed = "free_delayed"
    if not realtime_entitled:
        provider_label = "plan-blocked"

    return {
        "enabled": settings.realtime_stream_enabled and realtime_entitled,
        "provider": provider_label,
        "configured": configured,
        "true_tick_supported": true_tick_supported,
        "feed": feed,
        "sandbox": settings.alpaca_use_sandbox,
        "supported_channels": list(SUPPORTED_CHANNELS),
        "max_tickers": settings.realtime_max_tickers,
        "connection_mode": (
            "provider_websocket"
            if true_tick_supported
            else "internal_polling"
            if internal_polling_supported
            else "plan_blocked"
            if not realtime_entitled
            else "unavailable"
        ),
        "entitlement_blocked": not realtime_entitled,
        "data_quality": "tick" if true_tick_supported else "delayed_or_research" if internal_polling_supported else "unavailable",
        "real_time_quotes": true_tick_supported,
    }


def _internal_owned_polling_supported(*, realtime_entitled: bool) -> bool:
    if not (settings.realtime_stream_enabled and realtime_entitled):
        return False
    if not bool(getattr(settings, "internal_owned_api_enabled", False)):
        return False

    internal_modes = {
        "internal",
        "internal_paper",
        "internal_simulator",
        "legitimate",
        "legitimate_brokerage",
        "legitimate_brokerage_paper",
    }
    broker_mode = str(getattr(settings, "broker_mode", "") or "").strip().lower()
    paper_provider = str(getattr(settings, "paper_broker_provider", "") or "").strip().lower()
    execution_adapter = str(getattr(settings, "execution_adapter", "") or "").strip().lower()
    if not any(value in internal_modes for value in (broker_mode, paper_provider, execution_adapter)):
        return False

    provider = str(settings.market_data_provider or "").strip().lower()
    return provider in {"free_delayed", "internal", "internal_owned_api", "yfinance"}


def _alpaca_stream_url() -> str:
    base_url = (
        settings.alpaca_market_data_ws_sandbox_url
        if settings.alpaca_use_sandbox
        else settings.alpaca_market_data_ws_url
    ).rstrip("/")
    return f"{base_url}/{settings.alpaca_stock_version}/{settings.alpaca_stock_feed}"


def _serialize_message(message_type: str, **payload: Any) -> str:
    return json.dumps({"type": message_type, **payload})


def _normalize_alpaca_event(message: dict[str, Any], *, feed: str) -> dict[str, Any] | None:
    message_type = str(message.get("T") or "").strip()
    symbol = str(message.get("S") or "").strip().upper()

    if message_type == "t":
        return {
            "type": "trade",
            "provider": "alpaca",
            "feed": feed,
            "symbol": symbol,
            "price": message.get("p"),
            "size": message.get("s"),
            "exchange": message.get("x"),
            "tape": message.get("z"),
            "timestamp": message.get("t"),
            "conditions": message.get("c", []),
            "trade_id": message.get("i"),
        }

    if message_type == "q":
        bid_price = message.get("bp")
        ask_price = message.get("ap")
        spread = None
        try:
            if bid_price is not None and ask_price is not None:
                spread = float(ask_price) - float(bid_price)
        except (TypeError, ValueError):
            spread = None

        return {
            "type": "quote",
            "provider": "alpaca",
            "feed": feed,
            "symbol": symbol,
            "bid_price": bid_price,
            "bid_size": message.get("bs"),
            "bid_exchange": message.get("bx"),
            "ask_price": ask_price,
            "ask_size": message.get("as"),
            "ask_exchange": message.get("ax"),
            "spread": spread,
            "timestamp": message.get("t"),
            "conditions": message.get("c", []),
            "tape": message.get("z"),
        }

    return None


@dataclass(slots=True)
class _StreamSubscriber:
    subscriber_id: int
    websocket: WebSocket
    tickers: tuple[str, ...]
    channels: tuple[str, ...]
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(maxsize=256))

    def wants_event(self, event: dict[str, Any]) -> bool:
        event_type = str(event.get("type") or "").strip().lower()
        symbol = str(event.get("symbol") or "").strip().upper()
        return bool(symbol) and symbol in self.tickers and event_type in self.channels


class _SharedAlpacaStreamManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._subscribers: dict[int, _StreamSubscriber] = {}
        self._next_subscriber_id = 1
        self._upstream_task: asyncio.Task[None] | None = None
        self._generation = 0
        self._active_subscription: dict[str, tuple[str, ...]] = {
            "trades": tuple(),
            "quotes": tuple(),
        }
        self._last_status: dict[str, Any] | None = None

    async def register(self, websocket: WebSocket, *, tickers: list[str], channels: list[str]) -> _StreamSubscriber:
        subscriber = _StreamSubscriber(
            subscriber_id=0,
            websocket=websocket,
            tickers=tuple(sorted(set(tickers))),
            channels=tuple(sorted(set(channels))),
        )

        status_snapshot: dict[str, Any] | None = None
        task_to_cancel: asyncio.Task[None] | None = None

        async with self._lock:
            subscriber.subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[subscriber.subscriber_id] = subscriber
            desired_subscription = self._desired_subscription_locked()
            if self._should_restart_upstream_locked(desired_subscription):
                task_to_cancel = self._swap_upstream_task_locked(desired_subscription)
            else:
                status_snapshot = dict(self._last_status or {"status": "live"})

        if task_to_cancel is not None:
            await self._cancel_task(task_to_cancel)
        if status_snapshot is not None:
            self._enqueue_message(
                subscriber,
                self._build_status_message(subscriber, **status_snapshot),
            )
        return subscriber

    async def unregister(self, subscriber_id: int) -> None:
        task_to_cancel: asyncio.Task[None] | None = None

        async with self._lock:
            removed = self._subscribers.pop(subscriber_id, None)
            if removed is None:
                return

            desired_subscription = self._desired_subscription_locked()
            if any(desired_subscription.values()):
                if self._should_restart_upstream_locked(desired_subscription):
                    task_to_cancel = self._swap_upstream_task_locked(desired_subscription)
            else:
                self._active_subscription = {"trades": tuple(), "quotes": tuple()}
                self._last_status = None
                if self._upstream_task is not None:
                    task_to_cancel = self._upstream_task
                    self._upstream_task = None
                    self._generation += 1

        if task_to_cancel is not None:
            await self._cancel_task(task_to_cancel)

    async def relay_to_client(self, subscriber: _StreamSubscriber) -> None:
        try:
            while True:
                payload = await subscriber.queue.get()
                await subscriber.websocket.send_text(payload)
        except WebSocketDisconnect:
            logger.info("Client disconnected from realtime stream.")
        except Exception:
            logger.exception("Realtime websocket relay failed.")

    def _enqueue_message(self, subscriber: _StreamSubscriber, payload: str) -> None:
        try:
            subscriber.queue.put_nowait(payload)
        except asyncio.QueueFull:
            with suppress(asyncio.QueueEmpty):
                subscriber.queue.get_nowait()
            with suppress(asyncio.QueueFull):
                subscriber.queue.put_nowait(payload)

    async def _cancel_task(self, task: asyncio.Task[None]) -> None:
        if task.done():
            with suppress(asyncio.CancelledError, Exception):
                await task
            return
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task

    def _desired_subscription_locked(self) -> dict[str, tuple[str, ...]]:
        combined: dict[str, set[str]] = {channel: set() for channel in SUPPORTED_CHANNELS}
        for subscriber in self._subscribers.values():
            for channel in subscriber.channels:
                combined[channel].update(subscriber.tickers)
        return {
            channel: tuple(sorted(values))
            for channel, values in combined.items()
        }

    def _should_restart_upstream_locked(self, desired_subscription: dict[str, tuple[str, ...]]) -> bool:
        current_task_running = self._upstream_task is not None and not self._upstream_task.done()
        return desired_subscription != self._active_subscription or not current_task_running

    def _swap_upstream_task_locked(self, desired_subscription: dict[str, tuple[str, ...]]) -> asyncio.Task[None] | None:
        previous = self._upstream_task
        self._generation += 1
        generation = self._generation
        self._active_subscription = desired_subscription
        self._last_status = {
            "status": "connecting_provider",
            "provider_subscription": self._provider_subscription_payload(desired_subscription),
        }
        if any(desired_subscription.values()):
            self._upstream_task = asyncio.create_task(
                self._run_upstream(generation, desired_subscription)
            )
        else:
            self._upstream_task = None
        return previous

    def _provider_subscription_payload(self, subscription: dict[str, tuple[str, ...]]) -> dict[str, list[str]]:
        payload: dict[str, list[str]] = {}
        for channel in SUPPORTED_CHANNELS:
            values = list(subscription.get(channel) or ())
            if values:
                payload[channel] = values
        return payload

    def _build_status_message(self, subscriber: _StreamSubscriber, status: str, **extra: Any) -> str:
        return _serialize_message(
            "stream_status",
            status=status,
            provider="alpaca",
            feed=settings.alpaca_stock_feed,
            tickers=list(subscriber.tickers),
            channels=list(subscriber.channels),
            **extra,
        )

    async def _broadcast_status(self, generation: int, status: str, **extra: Any) -> None:
        async with self._lock:
            if generation != self._generation:
                return
            self._last_status = {"status": status, **extra}
            subscribers = list(self._subscribers.values())

        for subscriber in subscribers:
            self._enqueue_message(
                subscriber,
                self._build_status_message(subscriber, status, **extra),
            )

    async def _broadcast_error(self, generation: int, message: str, *, details: dict[str, Any] | None = None) -> None:
        async with self._lock:
            if generation != self._generation:
                return
            subscribers = list(self._subscribers.values())

        for subscriber in subscribers:
            self._enqueue_message(
                subscriber,
                _serialize_message(
                    "stream_error",
                    provider="alpaca",
                    feed=settings.alpaca_stock_feed,
                    message=message,
                    details=details or {},
                ),
            )

    async def _broadcast_event(self, generation: int, event: dict[str, Any]) -> None:
        async with self._lock:
            if generation != self._generation:
                return
            subscribers = list(self._subscribers.values())

        payload = _serialize_message(
            "market_event",
            provider="alpaca",
            feed=settings.alpaca_stock_feed,
            event=event,
        )
        for subscriber in subscribers:
            if subscriber.wants_event(event):
                self._enqueue_message(subscriber, payload)

    async def _run_upstream(self, generation: int, subscription: dict[str, tuple[str, ...]]) -> None:
        stream_url = _alpaca_stream_url()
        subscribe_message: dict[str, Any] = {"action": "subscribe"}
        if subscription.get("trades"):
            subscribe_message["trades"] = list(subscription["trades"])
        if subscription.get("quotes"):
            subscribe_message["quotes"] = list(subscription["quotes"])

        while True:
            retry_delay_seconds = 2
            connection_limit_hit = False
            await self._broadcast_status(
                generation,
                "connecting_provider",
                provider_subscription=self._provider_subscription_payload(subscription),
            )
            try:
                async with websockets.connect(
                    stream_url,
                    open_timeout=10,
                    close_timeout=5,
                    ping_interval=15,
                    ping_timeout=15,
                    max_size=None,
                ) as upstream:
                    await upstream.send(
                        json.dumps(
                            {
                                "action": "auth",
                                "key": settings.alpaca_api_key_id,
                                "secret": settings.alpaca_api_secret_key,
                            }
                        )
                    )

                    async for raw_message in upstream:
                        try:
                            decoded = json.loads(raw_message)
                        except json.JSONDecodeError:
                            logger.warning("Ignored malformed upstream payload: %s", raw_message)
                            continue

                        messages = decoded if isinstance(decoded, list) else [decoded]
                        for message in messages:
                            message_type = str(message.get("T") or "").strip()
                            if message_type == "success":
                                upstream_status = str(message.get("msg") or "").strip().lower()
                                if upstream_status == "authenticated":
                                    await upstream.send(json.dumps(subscribe_message))
                                await self._broadcast_status(
                                    generation,
                                    upstream_status or "success",
                                    provider_subscription=self._provider_subscription_payload(subscription),
                                )
                                continue

                            if message_type == "subscription":
                                await self._broadcast_status(
                                    generation,
                                    "live",
                                    subscription=message,
                                    provider_subscription=self._provider_subscription_payload(subscription),
                                )
                                continue

                            if message_type == "error":
                                upstream_message = message.get("msg") or "Upstream stream error."
                                normalized_message = str(upstream_message).strip().lower()
                                if "connection limit exceeded" in normalized_message:
                                    logger.warning(
                                        "Realtime provider hit connection limit; keeping subscribers attached and retrying."
                                    )
                                    connection_limit_hit = True
                                    retry_delay_seconds = 6
                                    await self._broadcast_status(
                                        generation,
                                        "reconnecting_provider",
                                        reason="Realtime provider connection limit reached. Retrying shortly.",
                                        provider_error=upstream_message,
                                        provider_subscription=self._provider_subscription_payload(subscription),
                                    )
                                    break

                                raise RuntimeError(str(upstream_message))

                            normalized = _normalize_alpaca_event(message, feed=settings.alpaca_stock_feed)
                            if normalized is None:
                                continue

                            await self._broadcast_event(generation, normalized)

                if connection_limit_hit:
                    await asyncio.sleep(retry_delay_seconds)
                    continue

                await self._broadcast_status(
                    generation,
                    "reconnecting_provider",
                    reason="Realtime provider connection closed. Reconnecting.",
                    provider_subscription=self._provider_subscription_payload(subscription),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Shared realtime provider stream failed.")
                await self._broadcast_error(
                    generation,
                    "Realtime provider connection failed.",
                    details={"error": str(exc)},
                )
                await self._broadcast_status(
                    generation,
                    "reconnecting_provider",
                    reason="Realtime provider connection interrupted. Reconnecting.",
                    provider_subscription=self._provider_subscription_payload(subscription),
                )

            await asyncio.sleep(retry_delay_seconds)


_shared_alpaca_stream_manager = _SharedAlpacaStreamManager()


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(code=message.get("code", 1000))


def _is_closed_websocket_send_error(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    return "websocket.send" in message and (
        "after sending" in message or "already completed" in message
    )


async def _safe_send_websocket_text(websocket: WebSocket, payload: str) -> bool:
    try:
        await websocket.send_text(payload)
        return True
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        if _is_closed_websocket_send_error(exc):
            logger.info("Realtime websocket closed before an internal polling message could be sent.")
            return False
        raise


async def _drain_disconnect_task(disconnect_task: asyncio.Task[None]) -> None:
    with suppress(WebSocketDisconnect, asyncio.CancelledError, Exception):
        await disconnect_task


async def _run_internal_polling_stream(websocket: WebSocket, *, tickers: list[str], channels: list[str]) -> None:
    disconnect_task = asyncio.create_task(_wait_for_disconnect(websocket))
    if not await _safe_send_websocket_text(
        websocket,
        _serialize_message(
            "stream_status",
            status="internal_polling",
            provider="internal_owned_api",
            feed="free_delayed",
            data_quality="delayed_or_research",
            real_time_quotes=False,
            tickers=tickers,
            channels=channels,
            reason="Internal API polling is active. Quotes are delayed or research-grade, not licensed tick data.",
        )
    ):
        disconnect_task.cancel()
        await _drain_disconnect_task(disconnect_task)
        return

    poll_seconds = max(3, int(getattr(settings, "internal_stream_poll_seconds", 15) or 15))
    try:
        while True:
            if disconnect_task.done():
                await _drain_disconnect_task(disconnect_task)
                return
            try:
                from backend import stock_direction_model as sdm

                price_task = asyncio.create_task(asyncio.to_thread(sdm.batch_get_live_prices, tickers))
                done, _pending = await asyncio.wait(
                    {price_task, disconnect_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect_task in done:
                    price_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await price_task
                    await _drain_disconnect_task(disconnect_task)
                    return
                prices = await price_task
            except Exception as exc:  # pragma: no cover - defensive stream resilience
                logger.exception("Internal polling stream price refresh failed.")
                if not await _safe_send_websocket_text(
                    websocket,
                    _serialize_message(
                        "stream_error",
                        provider="internal_owned_api",
                        feed="free_delayed",
                        message="Internal API polling refresh failed.",
                        details={"error": str(exc)},
                    )
                ):
                    return
                prices = {}

            timestamp = datetime.now(timezone.utc).isoformat()
            for symbol in tickers:
                if disconnect_task.done():
                    await _drain_disconnect_task(disconnect_task)
                    return
                price = prices.get(symbol)
                if price is None:
                    continue
                if "trades" in channels:
                    if not await _safe_send_websocket_text(
                        websocket,
                        _serialize_message(
                            "market_event",
                            provider="internal_owned_api",
                            feed="free_delayed",
                            event={
                                "type": "trade",
                                "provider": "internal_owned_api",
                                "feed": "free_delayed",
                                "symbol": symbol,
                                "price": float(price),
                                "size": None,
                                "timestamp": timestamp,
                                "source": "internal_polling",
                                "realtime": False,
                                "delayed": True,
                            },
                        )
                    ):
                        return
                if "quotes" in channels:
                    if disconnect_task.done():
                        await _drain_disconnect_task(disconnect_task)
                        return
                    if not await _safe_send_websocket_text(
                        websocket,
                        _serialize_message(
                            "market_event",
                            provider="internal_owned_api",
                            feed="free_delayed",
                            event={
                                "type": "quote",
                                "provider": "internal_owned_api",
                                "feed": "free_delayed",
                                "symbol": symbol,
                                "bid_price": None,
                                "ask_price": None,
                                "spread": None,
                                "last_price": float(price),
                                "timestamp": timestamp,
                                "source": "internal_polling",
                                "realtime": False,
                                "delayed": True,
                            },
                        )
                    ):
                        return

            done, _pending = await asyncio.wait({disconnect_task}, timeout=poll_seconds)
            if done:
                await _drain_disconnect_task(disconnect_task)
                return
    except WebSocketDisconnect:
        logger.info("Client disconnected from internal polling stream.")
    finally:
        disconnect_task.cancel()
        await _drain_disconnect_task(disconnect_task)


async def stream_market_data(
    websocket: WebSocket,
    *,
    tickers: list[str],
    channels: list[str],
    realtime_entitled: bool = True,
    entitlement_reason: str | None = None,
) -> None:
    capabilities = get_realtime_capabilities(realtime_entitled=realtime_entitled)

    await websocket.accept()
    if not await _safe_send_websocket_text(
        websocket,
        _serialize_message("stream_capabilities", **capabilities),
    ):
        return

    if not capabilities["enabled"]:
        message = entitlement_reason or "Realtime streaming is disabled for this API."
        if capabilities.get("entitlement_blocked"):
            message = entitlement_reason or "Realtime streaming is not enabled for the active tenant plan."
        await _safe_send_websocket_text(
            websocket,
            _serialize_message(
                "stream_error",
                message=message,
            )
        )
        with suppress(Exception):
            await websocket.close(code=4403)
        return

    if not tickers:
        await _safe_send_websocket_text(
            websocket,
            _serialize_message(
                "stream_error",
                message="At least one ticker is required for realtime streaming.",
            )
        )
        with suppress(Exception):
            await websocket.close(code=4400)
        return

    if settings.market_data_provider != "alpaca":
        if capabilities.get("connection_mode") == "internal_polling":
            await _run_internal_polling_stream(websocket, tickers=tickers, channels=channels)
            return
        await _safe_send_websocket_text(
            websocket,
            _serialize_message(
                "stream_error",
                message="True tick streaming is only configured for the Alpaca provider in this build.",
            )
        )
        with suppress(Exception):
            await websocket.close(code=4400)
        return

    if not (settings.alpaca_api_key_id and settings.alpaca_api_secret_key):
        await _safe_send_websocket_text(
            websocket,
            _serialize_message(
                "stream_error",
                message="Set APCA_API_KEY_ID and APCA_API_SECRET_KEY to enable tick-by-tick market streaming.",
            )
        )
        with suppress(Exception):
            await websocket.close(code=4401)
        return

    subscriber = await _shared_alpaca_stream_manager.register(
        websocket,
        tickers=tickers,
        channels=channels,
    )
    sender_task = asyncio.create_task(_shared_alpaca_stream_manager.relay_to_client(subscriber))
    disconnect_task = asyncio.create_task(_wait_for_disconnect(websocket))

    try:
        done, pending = await asyncio.wait(
            {sender_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        for task in done:
            with suppress(WebSocketDisconnect, asyncio.CancelledError, Exception):
                await task
    except WebSocketDisconnect:
        logger.info("Client disconnected from realtime stream.")
    finally:
        await _shared_alpaca_stream_manager.unregister(subscriber.subscriber_id)
        sender_task.cancel()
        disconnect_task.cancel()
        with suppress(WebSocketDisconnect, asyncio.CancelledError):
            await sender_task
        with suppress(WebSocketDisconnect, asyncio.CancelledError):
            await disconnect_task
        if websocket.client_state.name.upper() != "DISCONNECTED":
            with suppress(Exception):
                await websocket.close(code=1000)
