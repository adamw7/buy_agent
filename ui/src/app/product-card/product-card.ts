import {
  Component,
  ElementRef,
  afterRenderEffect,
  computed,
  input,
  linkedSignal,
  output,
  signal,
  viewChild,
} from '@angular/core';

import { screenshotUrl } from '../agent';
import type { RailOption, RankedProduct, Receipt, ScoreWeights } from '../agent.types';

/** The criteria a score is blended from, in the order they are weighted. */
const CRITERIA = [
  'rating',
  'popularity',
  'price',
] as const satisfies readonly (keyof ScoreWeights)[];

/** Where the keyboard goes in each block the payment area draws, in the order they are
 *  drawn: the cart a confirmation restates -- never the button under it that buys, or
 *  Enter pressed twice would be the single click the confirmation exists to prevent --
 *  the wait, the receipt, the Pay button a cancelled or failed payment comes back to,
 *  and the sentence saying what holds paying back where a refusal put one in its place. */
const LANDING = '.confirm p, .authorising, .receipt, button.pay, .held';

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

  /** Whether the server has a camera, so this card may ask for a picture (ADR-0065). */
  readonly screenshots = input(false);

  /** Whether this run may pay at all: the shopper asked for it and the server can. */
  readonly canPay = input(false);

  /** What holds paying back -- a paying setting the form marks, named -- or null. */
  readonly held = input<string | null>(null);

  /** The payment's rail row, which says whether anyone is charged. */
  readonly rail = input<RailOption | null>(null);

  /** The product a payment is in flight for, page-wide, or null. */
  readonly paying = input<string | null>(null);

  /** What came of paying for *this* product, once something did. */
  readonly receipt = input<Receipt | null>(null);

  /** The approval a person gave, emitted when they confirm. */
  readonly pay = output<{ title: string; price: number; currency: string }>();

  /** Whether this card is showing its confirmation -- closed again whenever what it
   *  restates changes under it. Hidden rather than closed, one opened under the dry run
   *  came back open when paying was ticked again, reading "you will be charged" over a
   *  rail that charges, one click from paying. */
  protected readonly confirming = linkedSignal({
    source: () => [this.canPay(), this.held(), this.rail()?.name],
    computation: () => false,
  });

  /** The payment area, every press inside which redraws it. */
  private readonly payment = viewChild<ElementRef<HTMLElement>>('payment');

  /** Whether the keyboard focus is in the payment area. */
  private focused = false;

  /** Whether any payment is in flight: one at a time, page-wide. */
  protected readonly locked = computed(() => this.paying() !== null);

  /** Whether the payment in flight is this card's, so it can say it is waiting. */
  protected readonly authorising = computed(() => this.paying() === this.product().name);

  /** Whether to offer the button: paying is on, this product can be paid, and
   *  nothing has been bought yet. */
  protected readonly offersPayment = computed(
    () =>
      this.canPay() &&
      this.held() === null &&
      this.product().cannot_pay === null &&
      this.receipt() === null,
  );

  /** What holds paying back, on a card that could otherwise be paid for. */
  protected readonly heldBack = computed(() =>
    this.canPay() && this.receipt() === null && this.product().cannot_pay === null
      ? this.held()
      : null,
  );

  /** Why this one cannot be bought, where the run could have bought something. */
  protected readonly refusal = computed(() =>
    this.canPay() && this.receipt() === null ? this.product().cannot_pay : null,
  );

  constructor() {
    // Each press in the payment area replaces the block it was in -- Pay with the
    // confirmation, Cancel with Pay, the confirmation with the wait, the wait with the
    // receipt -- and focus on an element that is gone is focus on nothing: the next Tab
    // went on to the next card, past "Yes, authorise it" and the Cancel beside it. So
    // it goes to what replaced it, wherever the reader had not moved it themselves.
    afterRenderEffect(() => {
      // What the template draws the area from.
      this.receipt();
      this.authorising();
      this.confirming();
      this.offersPayment();
      this.heldBack();
      const area = this.payment()?.nativeElement;
      const active = document.activeElement;
      if (area && this.focused && (active === null || active === document.body)) {
        area.querySelector<HTMLElement>(LANDING)?.focus({ preventScroll: true });
      }
    });
  }

  /** The keyboard focus came into the payment area. */
  protected entered(): void {
    this.focused = true;
  }

  /** The focus left the payment area for somewhere else. An area redrawn under the
   *  focus sends it nowhere, which is not the reader leaving. */
  protected left(event: FocusEvent): void {
    const next = event.relatedTarget;
    if (next instanceof Node && !this.payment()?.nativeElement.contains(next)) {
      this.focused = false;
    }
  }

  protected startConfirming(): void {
    this.confirming.set(true);
  }

  protected cancel(): void {
    this.confirming.set(false);
  }

  protected confirm(): void {
    const product = this.product();
    // The cart's currency, not the product's own (ADR-0043).
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

  /** The address whose picture failed; another address is asked again. */
  private readonly unphotographed = signal<string | null>(null);

  /** The picture's URL, or null: no camera, no link, or it already failed. */
  protected readonly shot = computed(() => {
    const url = this.product().url;
    if (!this.screenshots() || !url || url === this.unphotographed()) {
      return null;
    }
    return screenshotUrl(url);
  });

  /** The picture's alt text, which also names its link. */
  protected readonly shotLabel = computed(
    () => `Screenshot of the page at ${this.host() ?? this.product().url}`,
  );

  /** The picture failed: drop the frame rather than show a broken image. */
  protected lost(): void {
    this.unphotographed.set(this.product().url);
  }

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
