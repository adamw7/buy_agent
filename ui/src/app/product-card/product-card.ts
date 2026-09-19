import { Component, computed, input, output, signal } from '@angular/core';

import type { RailOption, RankedProduct, Receipt, ScoreWeights } from '../agent.types';

/** The criteria a score is blended from, in the order they are weighted. */
const CRITERIA = [
  'rating',
  'popularity',
  'price',
] as const satisfies readonly (keyof ScoreWeights)[];

/** One criterion behind the score, as the card draws it. */
interface ScoreShare {
  name: string;
  percent: number;
  weight: number | null;
  assumed: boolean;
}

/** One ranked product. */
@Component({
  selector: 'app-product-card',
  templateUrl: './product-card.html',
  styleUrl: './product-card.css',
  host: { '[class.highlighted]': 'highlighted()' },
})
export class ProductCard {
  readonly product = input.required<RankedProduct>();

  /** Whether this one made the top N the agent reports. */
  readonly highlighted = input(false);

  /** How much each criterion counted, as the run that produced this reported it. */
  readonly weights = input<ScoreWeights | null>(null);

  /** Whether this run may pay at all: the shopper asked for it and the server can. */
  readonly canPay = input(false);

  /** The rail the payment would go through, so the confirmation can say whether
   *  anybody is about to be charged. Python decides that, on the rail's row. */
  readonly rail = input<RailOption | null>(null);

  /** The product a payment is in flight for, anywhere on the page, or null for
   *  none. The name and not a boolean: one at a time is why every card's button
   *  goes dead, and which one is why exactly one of them has something to say. */
  readonly paying = input<string | null>(null);

  /** What came of paying for *this* product, once something did. */
  readonly receipt = input<Receipt | null>(null);

  /** The approval a person gave, emitted when they confirm. */
  readonly pay = output<{ title: string; price: number; currency: string }>();

  /** Whether this card is showing its confirmation. */
  protected readonly confirming = signal(false);

  /** Whether any payment is in flight: one at a time, page-wide, so every button
   *  on every card stands down until it lands. */
  protected readonly locked = computed(() => this.paying() !== null);

  /** Whether the payment in flight is *this* card's. Paying is two calls to a
   *  counterparty on a 30-second budget each, and until now the whole of what the
   *  page did about that was grey three buttons out: the one action on this page
   *  that moves money was the only one with nothing saying it was happening, on a
   *  wait longer than any of the ones that do. */
  protected readonly authorising = computed(() => this.paying() === this.product().name);

  /** Whether to offer the button at all: the run asked, the server can, this
   *  product has a price a source printed, and nothing has been bought yet. */
  protected readonly offersPayment = computed(
    () => this.canPay() && this.product().cannot_pay === null && this.receipt() === null,
  );

  /** Why this one cannot be bought, where the run could have bought something. */
  protected readonly refusal = computed(() =>
    this.canPay() && this.receipt() === null ? this.product().cannot_pay : null,
  );

  protected startConfirming(): void {
    this.confirming.set(true);
  }

  protected cancel(): void {
    this.confirming.set(false);
  }

  protected confirm(): void {
    const product = this.product();
    // `pay_currency` and not `currency`: the money a purchase is in is the run's, so a page that
    // printed a bare "329.00" leaves the product's own `null` while the cart is in USD all the same
    // (ADR-0043).
    if (product.price === null || product.pay_currency === null) {
      return;
    }
    this.confirming.set(false);
    this.pay.emit({ title: product.name, price: product.price, currency: product.pay_currency });
  }

  protected readonly percent = computed(() => Math.round(this.product().score * 100));

  /** The score with its unit on it. */
  protected readonly scoreLabel = computed(() => `${this.percent()}%`);

  /** What the score is made of, in the order the criteria are weighted. */
  protected readonly parts = computed<ScoreShare[]>(() => {
    const breakdown = this.product().breakdown;
    const weights = this.weights();
    const assumed = new Set(breakdown.neutral);
    return CRITERIA.map((name) => ({
      name,
      percent: Math.round(breakdown[name] * 100),
      weight: weights ? Math.round(weights[name] * 100) : null,
      assumed: assumed.has(name),
    }));
  });

  protected readonly host = computed(() => {
    const url = this.product().url;
    if (!url) {
      return null;
    }
    try {
      return new URL(url).hostname.replace(/^www\./, '');
    } catch {
      return null;
    }
  });
}
