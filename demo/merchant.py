"""A local counterparty for the ``http`` rail: it signs the checkout, checks both mandates
against the checkout it signed, and charges nobody.

The rail asks two things of whatever ``--merchant-url`` names (``buy_agent/rails.py``):

- ``POST {url}/checkout`` with ``{"checkout": <document>}``, answered with
  ``{"checkout_jwt": <the document, signed by the merchant>, "nonce": <a challenge>}``;
- ``POST {url}/payment`` with ``{"transaction_id", "checkout_mandate", "payment_mandate"}``,
  answered with ``{"paid": true|false, "detail": <why>}``.

This is those two shapes answered by something that means it. A merchant that accepted
anything would prove nothing about the mandates, so ``/payment`` refuses a transaction it
did not sign a checkout for, a Checkout Mandate over any checkout but that one, a Payment
Mandate whose amount, currency, payee or transaction is not the one it quoted, a chain
answering another nonce, and the same checkout presented twice. Everything it verifies,
it verifies with the AP2 SDK's own verifier rather than with ``buy_agent.mandates``: a
counterparty reusing the agent's code to check the agent's work is a mirror, not a check.

Like the rest of ``demo/`` it is started by a person, speaks HTTP through the standard
library, and is imported by nothing in the package or either suite.

    python -m demo.merchant                  # listen on 127.0.0.1:8765
    python -m demo.merchant --once           # pay one made-up cart through it, then stop
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys
import tempfile
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from buy_agent import mandates

logger = logging.getLogger("demo.merchant")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

#: The largest request body read, which is three tokens and some punctuation.
_MAX_BODY = 256 * 1024


@dataclass(frozen=True, slots=True)
class Quote:
    """A checkout this merchant signed, and everything a payment for it must match."""

    jwt: str
    nonce: str
    amount: int
    currency: str
    payee: str
    title: str


class Declined(Exception):
    """A payment this merchant understood and will not take, and the sentence why."""


class Merchant:
    """Both halves of the conversation, with no HTTP in them."""

    def __init__(self, *, agent_key: Any, issuer: Any | None) -> None:
        # Imported here, after ``main`` has asked whether they import at all.
        from jwcrypto.jwk import JWK  # pylint: disable=import-outside-toplevel

        self.agent_key = agent_key
        self.issuer = issuer
        self.key = JWK.generate(kty="EC", crv="P-256", kid="demo-merchant")
        self.quotes: dict[str, Quote] = {}
        self.lock = threading.Lock()

    # -- /checkout ---------------------------------------------------------------

    def checkout(self, body: dict[str, Any]) -> dict[str, str]:
        """Price the cart the agent described, sign it, and remember what was signed."""
        from ap2.sdk import jwt_helper, utils  # pylint: disable=import-outside-toplevel

        document = body.get("checkout")
        if not isinstance(document, dict):
            raise ValueError("expected {\"checkout\": {...}}")
        lines = document.get("line_items")
        if not isinstance(lines, list) or len(lines) != 1 or lines[0].get("quantity") != 1:
            raise ValueError("expected exactly one line item, of quantity 1")
        total = next(
            (t.get("amount") for t in document.get("totals", []) if t.get("type") == "total"),
            None,
        )
        currency = document.get("currency")
        if not isinstance(total, int) or total <= 0 or not isinstance(currency, str):
            raise ValueError("expected a positive integer total and a currency")
        payee = (document.get("merchant") or {}).get("id")
        if not isinstance(payee, str) or not payee:
            raise ValueError("expected a merchant with an id")

        # The order id is the merchant's to assign: the rail sends an empty one.
        signed = {**document, "id": f"order-{secrets.token_hex(4)}"}
        token = jwt_helper.create_jwt(
            {"alg": "ES256", "typ": "JWT", "kid": "demo-merchant"}, signed, self.key
        )
        transaction = utils.compute_sha256_b64url(token)
        quote = Quote(
            jwt=token,
            nonce=secrets.token_urlsafe(24),
            amount=total,
            currency=currency,
            payee=payee,
            title=str(lines[0].get("item", {}).get("title", "")),
        )
        with self.lock:
            self.quotes[transaction] = quote
        logger.info(
            "Signed %s for %s: %s %s to %s, transaction %s",
            signed["id"],
            quote.title,
            quote.amount,
            quote.currency,
            quote.payee,
            transaction,
        )
        return {"checkout_jwt": token, "nonce": quote.nonce}

    # -- /payment ----------------------------------------------------------------

    def payment(self, body: dict[str, Any]) -> dict[str, Any]:
        """Check both mandates against the checkout they claim, and answer for them."""
        transaction = str(body.get("transaction_id") or "")
        with self.lock:
            # Taken out whether or not it verifies: a checkout is quoted for one attempt,
            # so a chain replayed after a refusal is refused as well.
            quote = self.quotes.pop(transaction, None)
        try:
            if quote is None:
                raise Declined(
                    "no checkout signed here has that transaction id -- it was never "
                    "quoted, or it has been presented already"
                )
            self._check_checkout_mandate(body.get("checkout_mandate"), transaction, quote)
            self._check_payment_mandate(body.get("payment_mandate"), transaction, quote)
        except Declined as exc:
            logger.warning("Declined transaction %s: %s", transaction, exc)
            return {"paid": False, "detail": str(exc)}

        logger.info(
            "Accepted %s %s for %s, transaction %s -- charged nobody",
            quote.amount,
            quote.currency,
            quote.title,
            transaction,
        )
        return {
            "paid": True,
            "detail": (
                f"The demo merchant verified both mandates against the checkout it "
                f"signed for {quote.title} ({quote.amount} {quote.currency} in minor "
                f"units). It moves no money, so nothing was charged."
            ),
        }

    def _check_checkout_mandate(self, token: Any, transaction: str, quote: Quote) -> None:
        """The Checkout Mandate is the agent's signature over *this* signed checkout."""
        # pylint: disable-next=import-outside-toplevel
        from ap2.sdk.generated.checkout_mandate import CheckoutMandate

        payload = self._verified(token, self.agent_key, CheckoutMandate, "Checkout Mandate")
        if payload.checkout_jwt != quote.jwt:
            raise Declined("the Checkout Mandate is over a checkout this merchant did not sign")
        if payload.checkout_hash != transaction:
            raise Declined("the Checkout Mandate's checkout_hash is not this transaction")

    def _check_payment_mandate(self, token: Any, transaction: str, quote: Quote) -> None:
        """The Payment Mandate pays this merchant this amount, for this checkout."""
        # pylint: disable-next=import-outside-toplevel
        from ap2.sdk.generated.payment_mandate import PaymentMandate

        if self.issuer is None:
            # Human present: the agent signed the closed mandate itself, after a person
            # approved the cart.
            try:
                closed = self._verified(token, self.agent_key, PaymentMandate, "Payment Mandate")
            except Declined as exc:
                raise Declined(
                    f"{exc}. If the agent is buying on an open mandate, start this merchant "
                    f"with --mandate naming the same file"
                ) from exc
        else:
            # Human not present: a chain from the open mandate the shopper signed, which
            # has to answer the challenge this merchant issued with the checkout.
            closed = self._verified_chain(token, transaction, quote)

        if closed.transaction_id != transaction:
            raise Declined(
                "the Payment Mandate is bound to another checkout "
                f"(transaction {closed.transaction_id})"
            )
        amount = closed.payment_amount
        if (amount.amount, amount.currency) != (quote.amount, quote.currency):
            raise Declined(
                f"the Payment Mandate authorises {amount.amount} {amount.currency}, and "
                f"the checkout was for {quote.amount} {quote.currency}"
            )
        if closed.payee.id != quote.payee:
            raise Declined(
                f"the Payment Mandate pays {closed.payee.id}, and the checkout was "
                f"quoted by {quote.payee}"
            )

    @staticmethod
    def _verified(token: Any, key: Any, kind: type, name: str) -> Any:
        """One closed mandate, read back through the SDK's verifier or refused."""
        from ap2.sdk.mandate import MandateClient  # pylint: disable=import-outside-toplevel

        if not isinstance(token, str) or not token:
            raise Declined(f"no {name} was presented")
        try:
            return (
                MandateClient().verify(token=token, key_or_provider=key, payload_type=kind)
            ).mandate_payload
        except Exception as exc:  # every failure here is the one refusal
            raise Declined(f"the {name} did not verify ({exc})") from exc

    def _verified_chain(self, token: Any, transaction: str, quote: Quote) -> Any:
        """An open mandate and the agent's closing hop, verified as one chain."""
        # pylint: disable=import-outside-toplevel
        from ap2.sdk.mandate import MandateClient
        from ap2.sdk.payment_mandate_chain import PaymentMandateChain

        if not isinstance(token, str) or not token:
            raise Declined("no Payment Mandate was presented")
        try:
            chain = PaymentMandateChain.parse(
                MandateClient().verify(
                    token=token,
                    key_or_provider=lambda _token: self.issuer,
                    expected_aud=mandates.CREDENTIAL_PROVIDER_AUDIENCE,
                    expected_nonce=quote.nonce,
                )
            )
        except Exception as exc:  # every failure here is the one refusal
            raise Declined(f"the Payment Mandate chain did not verify ({exc})") from exc
        violations = chain.verify(expected_transaction_id=transaction)
        if violations:
            raise Declined("the open mandate does not allow this: " + "; ".join(violations))
        return chain.closed_mandate


def handler_for(merchant: Merchant) -> type[BaseHTTPRequestHandler]:
    """The two routes the rail speaks, and nothing else."""

    class Handler(BaseHTTPRequestHandler):
        """``POST /checkout`` and ``POST /payment``, as JSON."""

        # http.server's name for it, not ours.
        def do_POST(self) -> None:  # pylint: disable=invalid-name
            """Answer one of the two routes, or say which ones there are."""
            route = {"/checkout": merchant.checkout, "/payment": merchant.payment}.get(
                self.path.rstrip("/")
            )
            if route is None:
                self._send(404, {"detail": "this merchant answers /checkout and /payment"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= _MAX_BODY:
                    raise ValueError(f"expected a JSON body of at most {_MAX_BODY} bytes")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("expected a JSON object")
                answer = route(body)
            except ValueError as exc:
                # Malformed rather than declined: the rail reads a status like this as the
                # counterparty failing, which from here is exactly what it looks like.
                logger.warning("Refused %s: %s", self.path, exc)
                self._send(400, {"detail": str(exc)})
                return
            self._send(200, answer)

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
            logger.debug(format, *args)

    return Handler


def agent_key_from(location: str) -> Any:
    """The public half of the agent's enrolled key -- all a merchant is ever given."""
    from jwcrypto.jwk import JWK  # pylint: disable=import-outside-toplevel

    if not location:
        raise SystemExit(
            f"No agent key to verify against: give --agent-key, or set ${mandates.KEY_PATH} "
            f"as the agent's own run does. Make one with:  openssl ecparam -genkey -name "
            f"prime256v1 -noout -out agent-key.pem"
        )
    try:
        key = JWK.from_pem(Path(location).read_bytes())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Could not read the agent key at {location} ({exc}).") from exc
    return JWK(**json.loads(key.export_public()))


def issuer_from(location: str) -> Any | None:
    """The key an open mandate was issued under, where one is named."""
    from jwcrypto.jwk import JWK  # pylint: disable=import-outside-toplevel

    if not location:
        return None
    try:
        document = json.loads(Path(location).read_text(encoding="utf-8"))
        return JWK(**document["issuer_jwk"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"Could not read the open mandate at {location} ({exc}).") from exc


def serve(merchant: Merchant, host: str, port: int) -> ThreadingHTTPServer:
    """Bind the two routes; the caller decides whether to serve forever."""
    return ThreadingHTTPServer((host, port), handler_for(merchant))


def once() -> int:
    """Pay one made-up cart through the real rail against this merchant, then try the two
    things it must refuse. No model, no web, no key of anybody's: all three are made here.
    """
    # The package's paying half, imported after ``main`` has asked for the SDK it needs.
    # pylint: disable=import-outside-toplevel
    from buy_agent import rails
    from buy_agent.config import AgentConfig
    from buy_agent.payment import pay_for

    agent = mandates.generate_key("agent")
    with tempfile.TemporaryDirectory() as scratch:
        pem = Path(scratch) / "agent-key.pem"
        pem.write_bytes(agent.export_to_pem(private_key=True, password=None))
        # This process is both parties, so the agent's key is enrolled by handing the
        # merchant its public half and pointing the agent's own run at the private one.
        os.environ[mandates.KEY_PATH] = str(pem)
        os.environ.pop(mandates.MANDATE_PATH, None)
        merchant = Merchant(agent_key=agent_key_from(str(pem)), issuer=None)
        server = serve(merchant, DEFAULT_HOST, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        config = AgentConfig(
            pay=True,
            rail=rails.HTTP.name,
            merchant_url=f"http://{DEFAULT_HOST}:{server.server_address[1]}",
        )
        cart = _made_up_cart()
        try:
            receipt = pay_for(cart, config)
            print(f"paid:     {receipt.paid} -- {receipt.detail}")

            # Signed for 329.99, presented as 1.00: the amount no longer matches.
            signed, nonce = rails.HTTP.checkout(cart, config)
            cheaper = cart.model_copy(update={"price": 1.0, "amount": 100})
            tampered = mandates.authorise(cheaper, signed, key=agent, nonce=nonce)

            # A checkout paid for once, presented again.
            signed, nonce = rails.HTTP.checkout(cart, config)
            replayed = mandates.authorise(cart, signed, key=agent, nonce=nonce)
            rails.HTTP.settle(cart, replayed, config)

            refused = [_refused(label, cart, authorisation, config)
                       for label, authorisation in (("tampered", tampered), ("replayed", replayed))]
        finally:
            server.shutdown()
            server.server_close()
    return 0 if receipt.paid and all(refused) else 1


def _made_up_cart() -> Any:
    """What ``--once`` buys: nothing a search found, and priced like something that was."""
    from buy_agent.payment import Cart  # pylint: disable=import-outside-toplevel

    return Cart(
        title="Demo Headphones",
        price=329.99,
        currency="USD",
        amount=32999,
        merchant="AudioSite",
        url="https://audiosite.example/demo-headphones",
        item_id="demo-headphones",
    )


def _refused(label: str, cart: Any, authorisation: Any, config: Any) -> bool:
    """Present an authorisation that must be declined, and say whether it was."""
    # pylint: disable-next=import-outside-toplevel
    from buy_agent import rails
    from buy_agent.payment import PaymentError  # pylint: disable=import-outside-toplevel

    try:
        rails.HTTP.settle(cart, authorisation, config)
    except PaymentError as exc:
        print(f"{label}: refused -- {exc}")
        return True
    print(f"{label}: ACCEPTED, which a merchant checking the mandates never should")
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m demo.merchant",
        description=(
            "A local counterparty for --rail http: signs the checkout, verifies both "
            "mandates against it, charges nobody."
        ),
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"(default: {DEFAULT_HOST})")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"(default: {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--agent-key",
        default=os.getenv(mandates.KEY_PATH, ""),
        help=f"the agent's enrolled key, PEM (default: ${mandates.KEY_PATH})",
    )
    parser.add_argument(
        "--mandate",
        default=os.getenv(mandates.MANDATE_PATH, ""),
        help=(
            f"an open mandate file, for payments made while the shopper is not there "
            f"(default: ${mandates.MANDATE_PATH}; none means a person approves each one)"
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="pay one made-up cart through the http rail against this merchant, then stop",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log every request")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # The agent's own paying half logs a line per request under ``--once``.
    logging.getLogger("httpx").setLevel(logging.INFO if args.verbose else logging.WARNING)
    if not mandates.available():
        print(
            f"The demo merchant verifies mandates with the AP2 SDK, which is not "
            f"installed. Install it with:  {mandates.INSTALL}",
            file=sys.stderr,
        )
        return 1
    if args.once:
        return once()

    merchant = Merchant(agent_key=agent_key_from(args.agent_key), issuer=issuer_from(args.mandate))
    server = serve(merchant, args.host, args.port)
    url = f"http://{args.host}:{server.server_address[1]}"
    logger.info(
        "Demo merchant at %s, expecting %s. Point the agent at it with:  "
        "--pay --rail http --merchant-url %s",
        url,
        "chains from the open mandate" if merchant.issuer else "payments a person approved",
        url,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
