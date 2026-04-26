from __future__ import annotations

from typing import Any

import pandas as pd

from backend import stock_direction_model as sdm
from backend.core.config import settings
from backend.schemas import CloseTradeRequest, OpenTradeRequest, ReplaceOrderRequest
from backend.services.exceptions import ServiceError, ValidationServiceError
from backend.services.execution.alpaca_client import AlpacaApiError, AlpacaTradingClient, build_alpaca_paper_client
from backend.services.execution.base import ExecutionAdapter
from backend.services.execution.mappers import (
    BrokerExecutionError,
    build_alpaca_equity_order_payload,
    build_alpaca_option_order_payload,
    enrich_local_order_record,
    is_canceled_alpaca_status,
    is_expired_alpaca_status,
    is_filled_alpaca_status,
    is_rejected_alpaca_status,
    normalize_alpaca_status,
)
from backend.services.execution.types import (
    CancelOrderResult,
    ClosePositionResult,
    FillOrderResult,
    ReplaceOrderResult,
    SyncOrderResult,
    SubmitOrderResult,
)


class PaperLedgerPersistenceError(ServiceError):
    error_code = "ledger_persistence_failed"
    default_message = "Broker-paper lifecycle data did not persist into the local trade ledger."


class AlpacaPaperExecutionAdapter(ExecutionAdapter):
    def __init__(self, client: AlpacaTradingClient | None = None) -> None:
        self.client = client or build_alpaca_paper_client()

    @property
    def adapter_name(self) -> str:
        return "alpaca_paper"

    def _ensure_credentials(self) -> None:
        if not settings.alpaca_api_key_id or not settings.alpaca_api_secret_key:
            raise ValidationServiceError("Alpaca paper execution requires APCA_API_KEY_ID and APCA_API_SECRET_KEY.")

    @staticmethod
    def _normalize_identifier(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_entry_side(value: Any) -> str:
        normalized = str(value or "buy").strip().lower()
        return "sell" if normalized == "sell" else "buy"

    @classmethod
    def _resolve_close_side(cls, target_trade: dict[str, Any]) -> str:
        return "buy" if cls._normalize_entry_side(target_trade.get("broker_side")) == "sell" else "sell"

    def _matching_local_books(
        self,
        *,
        trade_id: str | None = None,
        order_id: str | None = None,
        route_correlation_id: str | None = None,
    ) -> list[str]:
        identifiers = {
            "trade_id": self._normalize_identifier(trade_id),
            "order_id": self._normalize_identifier(order_id),
            "route_correlation_id": self._normalize_identifier(route_correlation_id),
        }
        books: list[str] = []
        for book_name, frame in (
            ("open", sdm.read_open_trades()),
            ("pending", sdm.read_pending_orders()),
            ("closed", sdm.read_closed_trades()),
        ):
            if frame.empty:
                continue
            for column_name, identifier in identifiers.items():
                if not identifier:
                    continue
                series = frame.get(column_name, pd.Series("", index=frame.index)).astype(str).str.strip()
                if series.eq(identifier).any():
                    books.append(book_name)
                    break
        return sorted(set(books))

    def _assert_local_ledger_persisted(
        self,
        *,
        stage: str,
        expected_books: tuple[str, ...],
        trade_id: str | None = None,
        order_id: str | None = None,
        route_correlation_id: str | None = None,
    ) -> None:
        identifiers = {
            "trade_id": self._normalize_identifier(trade_id),
            "order_id": self._normalize_identifier(order_id),
            "route_correlation_id": self._normalize_identifier(route_correlation_id),
        }
        normalized_expected_books = tuple(
            str(value or "").strip().lower() for value in expected_books if str(value or "").strip()
        )
        if not normalized_expected_books:
            return

        def _frame_contains(frame: pd.DataFrame) -> bool:
            if frame.empty:
                return False
            mask = pd.Series(True, index=frame.index)
            matched = False
            for column_name, identifier in identifiers.items():
                if not identifier:
                    continue
                series = frame.get(column_name, pd.Series("", index=frame.index)).astype(str).str.strip()
                mask &= series.eq(identifier)
                matched = True
            return bool(matched and mask.any())

        frame_lookup = {
            "open": sdm.read_open_trades(),
            "pending": sdm.read_pending_orders(),
            "closed": sdm.read_closed_trades(),
        }
        for book_name in normalized_expected_books:
            if _frame_contains(frame_lookup.get(book_name, pd.DataFrame())):
                return

        expected_label = ", ".join(normalized_expected_books)
        raise PaperLedgerPersistenceError(
            f"Broker-paper lifecycle persistence failed during {stage}: no matching local {expected_label} row was found.",
            details={
                "collection_blocker": "ledger_persistence_failed",
                "broker": self.adapter_name,
                "stage": stage,
                "expected_books": list(normalized_expected_books),
                "matched_books": self._matching_local_books(
                    trade_id=trade_id,
                    order_id=order_id,
                    route_correlation_id=route_correlation_id,
                ),
                **identifiers,
            },
        )

    def _resolve_broker_order_id(self, internal_order_id: str) -> str:
        pending_orders = sdm.read_pending_orders()
        if pending_orders.empty or "order_id" not in pending_orders.columns:
            raise ValidationServiceError("Working broker order could not be found in the local desk.")

        matches = pending_orders["order_id"].astype(str).str.strip() == str(internal_order_id or "").strip()
        if not matches.any():
            raise ValidationServiceError("Working broker order was not found.")

        row = pending_orders.loc[pending_orders.index[matches][0]].to_dict()
        broker_order_id = str(row.get("broker_order_id") or "").strip()
        if not broker_order_id:
            raise ValidationServiceError("Working order is missing the broker order id.")
        return broker_order_id

    @staticmethod
    def _coerce_number(value: Any) -> float | None:
        if value in (None, "", "nan"):
            return None
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            return None
        return normalized if pd.notna(normalized) else None

    @staticmethod
    def _resolve_close_quantity(quantity: float, close_fraction: float) -> float:
        normalized_quantity = max(float(quantity or 0.0), 0.0)
        normalized_fraction = min(max(float(close_fraction or 1.0), 0.0), 1.0)
        if normalized_quantity <= 0 or normalized_fraction <= 0:
            return 0.0
        if normalized_fraction >= 1:
            return normalized_quantity
        if normalized_quantity <= 1:
            return min(
                normalized_quantity,
                max(0.001, float(int((normalized_quantity * normalized_fraction) * 1000)) / 1000.0),
            )
        close_quantity = max(1.0, float(int(round(normalized_quantity * normalized_fraction))))
        return min(close_quantity, normalized_quantity)

    def _build_fill_slippage(self, pending_order: dict[str, Any], broker_order: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
        expected = self._coerce_number(pending_order.get("limit_price"))
        if expected is None or expected <= 0:
            instrument_type = str(pending_order.get("instrument_type") or "listed_option").strip().lower()
            if instrument_type == "listed_option":
                expected = self._coerce_number(pending_order.get("contract_mid_at_open"))
            if expected is None or expected <= 0:
                expected = self._coerce_number(pending_order.get("live_price_at_submit"))
        actual = self._coerce_number(broker_order.get("filled_avg_price"))
        if expected is None or expected <= 0 or actual is None:
            return expected, actual, None, None
        slippage_dollars = float(actual - expected)
        slippage_bps = float((slippage_dollars / expected) * 10000.0)
        return expected, actual, slippage_dollars, slippage_bps

    @staticmethod
    def _resolve_contract_symbol(
        *,
        request: OpenTradeRequest | ReplaceOrderRequest,
        report: dict[str, Any] | None = None,
        order_ticket: dict[str, Any] | None = None,
        row: dict[str, Any] | None = None,
    ) -> str:
        recommended_contract = dict((report or {}).get("option_plan") or {}).get("recommended_contract") or {}
        for candidate in (
            getattr(request, "contract_symbol", None),
            (order_ticket or {}).get("contract_symbol"),
            (row or {}).get("contract_symbol"),
            recommended_contract.get("contract_symbol"),
        ):
            normalized = str(candidate or "").strip().upper()
            if normalized:
                return normalized
        raise ValidationServiceError("A valid option contract symbol is required before routing a listed option order.")

    def _apply_filled_record_pricing(
        self,
        *,
        record: dict[str, Any],
        broker_order: dict[str, Any],
    ) -> dict[str, Any]:
        updated = dict(record)
        fill_price = self._coerce_number(broker_order.get("filled_avg_price"))
        if fill_price is None or fill_price <= 0:
            return updated

        quantity = float(pd.to_numeric(updated.get("suggested_contracts"), errors="coerce") or 0.0)
        instrument_type = str(updated.get("instrument_type") or "listed_option").strip().lower()
        if instrument_type == "equity":
            updated["live_price_at_open"] = float(fill_price)
            updated["contract_mid_at_open"] = float(fill_price / 100.0)
            updated["position_cost"] = float(quantity * fill_price)
            return updated

        updated["contract_mid_at_open"] = float(fill_price)
        updated["position_cost"] = float(quantity * fill_price * 100.0)
        return updated

    def _build_submit_payload(
        self,
        *,
        request: OpenTradeRequest | ReplaceOrderRequest,
        report: dict[str, Any] | None = None,
        position: dict[str, Any] | None = None,
        order_ticket: dict[str, Any] | None = None,
        row: dict[str, Any] | None = None,
        client_order_id: str | None = None,
        side: str = "buy",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        instrument_type = str(
            getattr(request, "instrument_type", None)
            or (order_ticket or {}).get("instrument_type")
            or (row or {}).get("instrument_type")
            or "listed_option"
        ).strip().lower()
        quantity = float((position or {}).get("suggested_contracts") or (row or {}).get("suggested_contracts") or 0.0)

        if instrument_type == "listed_option":
            contract_symbol = self._resolve_contract_symbol(
                request=request,
                report=report,
                order_ticket=order_ticket,
                row=row,
            )
            payload = build_alpaca_option_order_payload(
                request,
                contract_symbol=contract_symbol,
                quantity=quantity,
                client_order_id=client_order_id,
                side=side,
            )
            return payload, {"class": "option", "fractionable": False}

        ticker = str(getattr(request, "ticker", None) or (row or {}).get("ticker") or "").strip().upper()
        try:
            asset_metadata = self.client.get_asset(ticker)
        except AlpacaApiError as exc:
            raise BrokerExecutionError(
                str(exc),
                status_code=400 if exc.status_code and exc.status_code < 500 else 502,
                details={"broker": self.adapter_name, "payload": exc.payload, "status_code": exc.status_code},
            ) from exc

        if quantity < 1 and not bool(asset_metadata.get("fractionable")):
            raise ValidationServiceError(f"{ticker} is not marked as fractionable for Alpaca paper routing.")

        payload = build_alpaca_equity_order_payload(
            request,
            ticker=ticker,
            quantity=quantity,
            client_order_id=client_order_id,
            side=side,
        )
        return payload, asset_metadata

    def submit_order(
        self,
        *,
        request: OpenTradeRequest,
        report: dict[str, Any],
        live_price: float,
        position: dict[str, Any],
        trade_id: str,
        order_id: str,
        order_ticket: dict[str, Any],
    ) -> SubmitOrderResult:
        self._ensure_credentials()
        quantity = float(position.get("suggested_contracts", 0) or 0.0)
        route_correlation_id = self._normalize_identifier(order_ticket.get("route_correlation_id"))
        payload, asset_metadata = self._build_submit_payload(
            request=request,
            report=report,
            position=position,
            order_ticket=order_ticket,
            client_order_id=order_id,
            side=self._normalize_entry_side(
                order_ticket.get("broker_side")
                or getattr(request, "broker_side", "buy")
            ),
        )

        try:
            broker_order = self.client.submit_order(payload)
        except AlpacaApiError as exc:
            raise BrokerExecutionError(
                str(exc),
                status_code=400 if exc.status_code and exc.status_code < 500 else 502,
                details={"broker": self.adapter_name, "payload": exc.payload, "status_code": exc.status_code},
            ) from exc

        filled = is_filled_alpaca_status(broker_order.get("status"))
        if filled:
            record = sdm.open_trade_record(
                report,
                float(live_price),
                position,
                order_ticket,
                trade_id=trade_id,
                order_id=order_id,
            )
            record = enrich_local_order_record(
                record,
                broker_name=self.adapter_name,
                broker_order=broker_order,
                asset_metadata=asset_metadata,
            )
            record = self._apply_filled_record_pricing(record=record, broker_order=broker_order)
            sdm.append_open_trade(record)
            self._assert_local_ledger_persisted(
                stage="submit_order_filled",
                expected_books=("open",),
                trade_id=trade_id,
                order_id=order_id,
                route_correlation_id=route_correlation_id,
            )
            return SubmitOrderResult(
                position_opened=True,
                record=record,
                pending_order=None,
                broker_name=self.adapter_name,
                broker_order_id=record.get("broker_order_id"),
                broker_status=record.get("broker_status"),
                broker_response=broker_order,
            )

        pending_order = sdm.pending_order_record(
            report,
            float(live_price),
            position,
            order_ticket,
            trade_id=trade_id,
            order_id=order_id,
        )
        pending_order = enrich_local_order_record(
            pending_order,
            broker_name=self.adapter_name,
            broker_order=broker_order,
            asset_metadata=asset_metadata,
        )
        sdm.append_pending_order(pending_order)
        self._assert_local_ledger_persisted(
            stage="submit_order_pending",
            expected_books=("pending",),
            trade_id=trade_id,
            order_id=order_id,
            route_correlation_id=route_correlation_id,
        )
        return SubmitOrderResult(
            position_opened=False,
            record=pending_order,
            pending_order=pending_order,
            broker_name=self.adapter_name,
            broker_order_id=pending_order.get("broker_order_id"),
            broker_status=pending_order.get("broker_status"),
            broker_response=broker_order,
        )

    def close_position(
        self,
        *,
        request: CloseTradeRequest,
        target_trade: dict[str, Any],
    ) -> ClosePositionResult:
        self._ensure_credentials()
        close_fraction = float(getattr(request, "close_fraction", 1.0) or 1.0)
        position_quantity = float(target_trade.get("suggested_contracts", 0) or 0.0)
        close_quantity = self._resolve_close_quantity(position_quantity, close_fraction)
        trade_id = self._normalize_identifier(target_trade.get("trade_id"))
        order_id = self._normalize_identifier(target_trade.get("order_id"))
        route_correlation_id = self._normalize_identifier(target_trade.get("route_correlation_id"))
        if close_quantity <= 0:
            raise ValidationServiceError("A positive quantity is required to close the broker-paper position.")
        instrument_type = str(target_trade.get("instrument_type") or "listed_option").strip().lower()
        close_side = self._resolve_close_side(target_trade)
        try:
            if instrument_type == "listed_option":
                contract_symbol = str(target_trade.get("contract_symbol") or "").strip().upper()
                if not contract_symbol:
                    raise ValidationServiceError("Working option position is missing its contract symbol.")
                broker_response = self.client.submit_order(
                    build_alpaca_option_order_payload(
                        ReplaceOrderRequest(
                            instrument_type="listed_option",
                            contract_symbol=contract_symbol,
                            order_type="limit",
                            time_in_force="day",
                            limit_price=float(getattr(request, "close_limit_price", None) or request.close_contract_mid),
                        ),
                        contract_symbol=contract_symbol,
                        quantity=close_quantity,
                        side="sell",
                        position_effect="close",
                    )
                )
            else:
                if close_quantity < 1 and target_trade.get("broker_fractionable") is False:
                    raise ValidationServiceError(
                        f"{target_trade.get('ticker') or 'This symbol'} is not marked as fractionable for partial broker-paper closes."
                    )
                ticker = str(target_trade.get("ticker") or "").strip().upper()
                if close_fraction >= 1.0 and hasattr(self.client, "close_position"):
                    broker_response = self.client.close_position(ticker)
                else:
                    broker_response = self.client.submit_order(
                        build_alpaca_equity_order_payload(
                            ReplaceOrderRequest(
                                instrument_type="equity",
                                order_type="market",
                                time_in_force="day",
                            ),
                            ticker=ticker,
                            quantity=close_quantity,
                            side=close_side,
                        )
                    )
        except AlpacaApiError as exc:
            raise BrokerExecutionError(
                str(exc),
                status_code=400 if exc.status_code and exc.status_code < 500 else 502,
                details={"broker": self.adapter_name, "payload": exc.payload, "status_code": exc.status_code},
            ) from exc

        broker_filled_avg_price = self._coerce_number(broker_response.get("filled_avg_price"))
        close_underlying_price = float(request.close_underlying_price)
        close_contract_mid = float(request.close_contract_mid)
        if broker_filled_avg_price is not None and broker_filled_avg_price > 0:
            if instrument_type == "equity":
                close_underlying_price = float(broker_filled_avg_price)
                close_contract_mid = float(broker_filled_avg_price / 100.0)
            else:
                close_contract_mid = float(broker_filled_avg_price)

        closed_trade = sdm.close_trade_by_index(
            trade_index=request.trade_index,
            close_underlying_price=close_underlying_price,
            close_contract_mid=close_contract_mid,
            close_fraction=close_quantity / position_quantity if position_quantity > 0 else 1.0,
            close_updates={
                "broker_name": self.adapter_name,
                "broker_close_order_id": broker_response.get("id"),
                "broker_close_status": str(broker_response.get("status") or "").strip().lower() or None,
            },
        )
        if closed_trade is None:
            raise PaperLedgerPersistenceError(
                "Broker-paper close completed, but the local closed-trades ledger row was not written.",
                details={
                    "collection_blocker": "ledger_persistence_failed",
                    "broker": self.adapter_name,
                    "stage": "close_position",
                    "expected_books": ["closed"],
                    "trade_id": trade_id,
                    "order_id": order_id,
                    "route_correlation_id": route_correlation_id,
                },
            )
        self._assert_local_ledger_persisted(
            stage="close_position_closed",
            expected_books=("closed",),
            trade_id=trade_id,
            order_id=order_id,
            route_correlation_id=route_correlation_id,
        )
        if close_quantity < position_quantity:
            self._assert_local_ledger_persisted(
                stage="close_position_remaining_open",
                expected_books=("open",),
                trade_id=trade_id,
                order_id=order_id,
                route_correlation_id=route_correlation_id,
            )
        enriched_trade = dict(closed_trade)
        return ClosePositionResult(
            closed_trade=enriched_trade,
            broker_name=self.adapter_name,
            broker_order_id=str(broker_response.get("id") or "").strip() or None,
            broker_status=str(broker_response.get("status") or "").strip().lower() or None,
            broker_response=broker_response,
        )

    def cancel_order(self, *, order_id: str) -> CancelOrderResult | None:
        self._ensure_credentials()
        pending_order = sdm.read_pending_orders()
        if pending_order.empty:
            return None
        broker_order_id = self._resolve_broker_order_id(order_id)
        try:
            broker_response = self.client.cancel_order(broker_order_id)
        except AlpacaApiError as exc:
            raise BrokerExecutionError(
                str(exc),
                status_code=400 if exc.status_code and exc.status_code < 500 else 502,
                details={"broker": self.adapter_name, "payload": exc.payload, "status_code": exc.status_code},
            ) from exc

        canceled = sdm.cancel_pending_order(order_id)
        if canceled is None:
            return None
        canceled = dict(canceled)
        canceled["broker_name"] = self.adapter_name
        canceled["broker_status"] = str(broker_response.get("status") or "canceled").strip().lower()
        return CancelOrderResult(
            canceled_order=canceled,
            broker_name=self.adapter_name,
            broker_order_id=broker_order_id,
            broker_status=canceled.get("broker_status"),
            broker_response=broker_response,
        )

    def replace_order(
        self,
        *,
        order_id: str,
        request: ReplaceOrderRequest,
        order_ticket: dict[str, Any],
    ) -> ReplaceOrderResult | None:
        self._ensure_credentials()
        pending_orders = sdm.read_pending_orders()
        if pending_orders.empty or "order_id" not in pending_orders.columns:
            return None
        matches = pending_orders["order_id"].astype(str).str.strip() == str(order_id or "").strip()
        if not matches.any():
            return None
        row = pending_orders.loc[pending_orders.index[matches][0]].to_dict()
        broker_order_id = self._resolve_broker_order_id(order_id)
        payload, _asset_metadata = self._build_submit_payload(
            request=request,
            report=None,
            row=row,
            order_ticket=order_ticket,
            side=self._normalize_entry_side((order_ticket or {}).get("broker_side") or row.get("broker_side")),
        )
        payload.pop("client_order_id", None)
        try:
            broker_response = self.client.replace_order(broker_order_id, payload)
        except AlpacaApiError as exc:
            raise BrokerExecutionError(
                str(exc),
                status_code=400 if exc.status_code and exc.status_code < 500 else 502,
                details={"broker": self.adapter_name, "payload": exc.payload, "status_code": exc.status_code},
            ) from exc

        updated = sdm.replace_pending_order(
            order_id,
            {
                **order_ticket,
                "broker_name": self.adapter_name,
                "broker_order_id": str(broker_response.get("id") or "").strip() or broker_order_id,
                "broker_status": str(broker_response.get("status") or "").strip().lower() or None,
                "broker_client_order_id": str(broker_response.get("client_order_id") or "").strip() or None,
                "broker_qty": broker_response.get("qty"),
                "broker_filled_qty": broker_response.get("filled_qty"),
                "broker_notional": broker_response.get("notional"),
                "broker_filled_avg_price": broker_response.get("filled_avg_price"),
                "broker_submitted_at": broker_response.get("submitted_at"),
                "broker_updated_at": broker_response.get("updated_at"),
            },
        )
        if updated is None:
            return None
        return ReplaceOrderResult(
            updated_order=updated,
            broker_name=self.adapter_name,
            broker_order_id=updated.get("broker_order_id"),
            broker_status=updated.get("broker_status"),
            broker_response=broker_response,
        )

    def fill_order(self, *, order_id: str, live_price: float) -> FillOrderResult | None:
        self._ensure_credentials()
        pending_orders = sdm.read_pending_orders()
        pending_row = None
        if not pending_orders.empty and "order_id" in pending_orders.columns:
            matches = pending_orders["order_id"].astype(str).str.strip() == str(order_id or "").strip()
            if matches.any():
                pending_row = pending_orders.loc[pending_orders.index[matches][0]].to_dict()
        broker_order_id = self._resolve_broker_order_id(order_id)
        try:
            broker_order = self.client.get_order(broker_order_id)
        except AlpacaApiError as exc:
            raise BrokerExecutionError(
                str(exc),
                status_code=400 if exc.status_code and exc.status_code < 500 else 502,
                details={"broker": self.adapter_name, "payload": exc.payload, "status_code": exc.status_code},
            ) from exc

        if not is_filled_alpaca_status(broker_order.get("status")):
            raise ValidationServiceError(
                f"Broker order is still {str(broker_order.get('status') or 'working').replace('_', ' ')}. Sync it after the fill completes."
            )

        fill_price = broker_order.get("filled_avg_price")
        instrument_type = str((pending_row or {}).get("instrument_type") or "listed_option").strip().lower()
        normalized_fill_price = float(live_price)
        if instrument_type == "equity" and fill_price not in (None, "", "nan"):
            normalized_fill_price = float(fill_price)
        route_correlation_id = self._normalize_identifier((pending_row or {}).get("route_correlation_id"))
        filled = sdm.fill_pending_order(order_id, normalized_fill_price)
        if filled is None:
            raise PaperLedgerPersistenceError(
                "Broker-paper fill completed, but the pending order was not converted into a local open trade.",
                details={
                    "collection_blocker": "ledger_persistence_failed",
                    "broker": self.adapter_name,
                    "stage": "fill_order",
                    "expected_books": ["open"],
                    "trade_id": self._normalize_identifier((pending_row or {}).get("trade_id")),
                    "order_id": self._normalize_identifier(order_id),
                    "route_correlation_id": route_correlation_id,
                },
            )
        filled = dict(filled)
        filled["broker_name"] = self.adapter_name
        filled["broker_order_id"] = broker_order_id
        filled["broker_status"] = str(broker_order.get("status") or "").strip().lower() or "filled"
        filled["broker_filled_qty"] = broker_order.get("filled_qty")
        filled["broker_filled_avg_price"] = broker_order.get("filled_avg_price")
        filled = self._apply_filled_record_pricing(record=filled, broker_order=broker_order)
        updated_open = sdm.update_open_trade(
            filled,
            trade_id=self._normalize_identifier(filled.get("trade_id")) or None,
            order_id=self._normalize_identifier(filled.get("order_id")) or self._normalize_identifier(order_id) or None,
        )
        if updated_open is None:
            raise PaperLedgerPersistenceError(
                "Broker-paper fill completed, but the local open trade could not be updated with broker fill details.",
                details={
                    "collection_blocker": "ledger_persistence_failed",
                    "broker": self.adapter_name,
                    "stage": "fill_order_update_open",
                    "expected_books": ["open"],
                    "trade_id": self._normalize_identifier(filled.get("trade_id")),
                    "order_id": self._normalize_identifier(filled.get("order_id")) or self._normalize_identifier(order_id),
                    "route_correlation_id": route_correlation_id,
                },
            )
        self._assert_local_ledger_persisted(
            stage="fill_order_persisted",
            expected_books=("open",),
            trade_id=self._normalize_identifier(updated_open.get("trade_id")),
            order_id=self._normalize_identifier(updated_open.get("order_id")) or self._normalize_identifier(order_id),
            route_correlation_id=route_correlation_id,
        )
        return FillOrderResult(
            filled_record=updated_open,
            broker_name=self.adapter_name,
            broker_order_id=broker_order_id,
            broker_status=updated_open.get("broker_status"),
            broker_response=broker_order,
        )

    def sync_order(self, *, pending_order: dict[str, Any]) -> SyncOrderResult | None:
        self._ensure_credentials()

        internal_order_id = str(pending_order.get("order_id") or "").strip()
        if not internal_order_id:
            raise ValidationServiceError("Working order is missing the local order id.")
        broker_order_id = str(pending_order.get("broker_order_id") or "").strip()
        if not broker_order_id:
            raise ValidationServiceError("Working order is missing the broker order id.")

        try:
            broker_order = self.client.get_order(broker_order_id)
        except AlpacaApiError as exc:
            raise BrokerExecutionError(
                str(exc),
                status_code=400 if exc.status_code and exc.status_code < 500 else 502,
                details={"broker": self.adapter_name, "payload": exc.payload, "status_code": exc.status_code},
            ) from exc

        broker_status = normalize_alpaca_status(broker_order.get("status")) or "unknown"
        enriched = enrich_local_order_record(
            dict(pending_order),
            broker_name=self.adapter_name,
            broker_order=broker_order,
        )

        if is_filled_alpaca_status(broker_status):
            fill_price = self._coerce_number(broker_order.get("filled_avg_price"))
            instrument_type = str(pending_order.get("instrument_type") or "listed_option").strip().lower()
            normalized_fill_price = float(self._coerce_number(pending_order.get("live_price_at_submit")) or 0.0)
            if instrument_type == "equity" and fill_price is not None:
                normalized_fill_price = float(fill_price)
            filled_record = sdm.fill_pending_order(internal_order_id, normalized_fill_price)
            if filled_record is None:
                raise PaperLedgerPersistenceError(
                    "Broker-paper sync observed a filled order, but the local pending row did not transition into open trades.",
                    details={
                        "collection_blocker": "ledger_persistence_failed",
                        "broker": self.adapter_name,
                        "stage": "sync_order_fill",
                        "expected_books": ["open"],
                        "trade_id": self._normalize_identifier(pending_order.get("trade_id")),
                        "order_id": internal_order_id,
                        "route_correlation_id": self._normalize_identifier(pending_order.get("route_correlation_id")),
                    },
                )
            filled_record = enrich_local_order_record(
                filled_record,
                broker_name=self.adapter_name,
                broker_order=broker_order,
            )
            filled_record = self._apply_filled_record_pricing(record=filled_record, broker_order=broker_order)
            expected, actual, slippage_dollars, slippage_bps = self._build_fill_slippage(pending_order, broker_order)
            if expected is not None:
                filled_record["expected_fill_price"] = expected
            if actual is not None:
                filled_record["actual_fill_price"] = actual
            if slippage_dollars is not None:
                filled_record["fill_slippage_dollars"] = round(float(slippage_dollars), 4)
            if slippage_bps is not None:
                filled_record["fill_slippage_bps"] = round(float(slippage_bps), 2)

            updated_open = sdm.update_open_trade(
                filled_record,
                trade_id=str(filled_record.get("trade_id") or "").strip() or None,
                order_id=internal_order_id,
            )
            if updated_open is None:
                raise PaperLedgerPersistenceError(
                    "Broker-paper sync observed a filled order, but the local open trade could not be updated with the fill details.",
                    details={
                        "collection_blocker": "ledger_persistence_failed",
                        "broker": self.adapter_name,
                        "stage": "sync_order_update_open",
                        "expected_books": ["open"],
                        "trade_id": self._normalize_identifier(filled_record.get("trade_id")),
                        "order_id": internal_order_id,
                        "route_correlation_id": self._normalize_identifier(filled_record.get("route_correlation_id")),
                    },
                )
            self._assert_local_ledger_persisted(
                stage="sync_order_persisted",
                expected_books=("open",),
                trade_id=self._normalize_identifier(updated_open.get("trade_id")),
                order_id=internal_order_id,
                route_correlation_id=self._normalize_identifier(updated_open.get("route_correlation_id")),
            )
            return SyncOrderResult(
                state="filled",
                opened_record=updated_open,
                broker_name=self.adapter_name,
                broker_order_id=broker_order_id,
                broker_status=broker_status,
                broker_response=broker_order,
                detail="Broker order filled and opened a live desk-tracked position.",
                slippage_dollars=round(float(slippage_dollars), 4) if slippage_dollars is not None else None,
                slippage_bps=round(float(slippage_bps), 2) if slippage_bps is not None else None,
            )

        if is_canceled_alpaca_status(broker_status) or is_expired_alpaca_status(broker_status) or is_rejected_alpaca_status(broker_status):
            terminal_order = sdm.cancel_pending_order(internal_order_id)
            if terminal_order is None:
                return None
            terminal_order = enrich_local_order_record(
                terminal_order,
                broker_name=self.adapter_name,
                broker_order=broker_order,
            )
            state = "canceled"
            detail = "Broker order was canceled and removed from the working book."
            if is_expired_alpaca_status(broker_status):
                state = "expired"
                detail = "Broker order expired before it filled."
            elif is_rejected_alpaca_status(broker_status):
                state = "rejected"
                detail = "Broker order was rejected and removed from the working book."
            return SyncOrderResult(
                state=state,
                terminal_order=terminal_order,
                broker_name=self.adapter_name,
                broker_order_id=broker_order_id,
                broker_status=broker_status,
                broker_response=broker_order,
                detail=detail,
            )

        enriched["order_status"] = broker_status.upper() or "WORKING"
        enriched["route_state"] = "accepted"
        enriched["book_state"] = "pending"
        enriched["status"] = "PENDING"
        updated_pending = sdm.update_pending_order(internal_order_id, enriched)
        if updated_pending is None:
            return None
        return SyncOrderResult(
            state="working",
            pending_order=updated_pending,
            broker_name=self.adapter_name,
            broker_order_id=broker_order_id,
            broker_status=broker_status,
            broker_response=broker_order,
            detail="Broker order is still working on the paper desk.",
        )
