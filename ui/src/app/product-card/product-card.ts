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

const CRITERIA = [
  'rating',
  'popularity',
  'price',
] as const satisfies readonly (keyof ScoreWeights)[];

/** Where the keyboard lands in each block the payment area draws: the cart, never the
 *  button that buys, or Enter pressed twice would pay. */
const LANDING = '.confirm p, .authorising, .receipt, button.pay, .held';

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

  readonly highlighted = input(false);
  readonly weights = input<ScoreWeights | null>(null);
  /** Whether the server has a camera (ADR-0065). */
  readonly screenshots = input(false);
  readonly canPay = input(false);
  /** The marked paying setting holding paying back, or null. */
  readonly held = input<string | null>(null);
  readonly rail = input<RailOption | null>(null);
  /** The product a payment is in flight for, page-wide. */
  readonly paying = input<string | null>(null);
  readonly receipt = input<Receipt | null>(null);
  /** The approval a person gave. */
  readonly pay = output<{ title: string; price: number; currency: string }>();

  /** The confirmation, closed whenever what it restates changes under it. */
  protected readonly confirming = linkedSignal({
    source: () => [this.canPay(), this.held(), this.rail()?.name],
    computation: () => false,
  });

  private readonly payment = viewChild<ElementRef<HTMLElement>>('payment');
  /** Whether the keyboard focus is in the payment area. */
  private focused = false;

  /** One payment at a time, page-wide. */
  protected readonly locked = computed(() => this.paying() !== null);
  protected readonly authorising = computed(() => this.paying() === this.product().name);

  protected readonly offersPayment = computed(
    () =>
      this.canPay() &&
      this.held() === null &&
      this.product().cannot_pay === null &&
      this.receipt() === null,
  );

  protected readonly heldBack = computed(() =>
    this.canPay() && this.receipt() === null && this.product().cannot_pay === null
      ? this.held()
      : null,
  );

  protected readonly refusal = computed(() =>
    this.canPay() && this.receipt() === null ? this.product().cannot_pay : null,
  );

  constructor() {
    // Each press replaces the block it was in, and focus on an element that is gone is
    // focus on nothing, so it goes to what replaced it unless the reader moved it.
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

  protected entered(): void {
    this.focused = true;
  }

  /** An area redrawn under the focus sends it nowhere, which is not leaving. */
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

  protected readonly scoreLabel = computed(() => `${this.percent()}%`);

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

  /** The address whose picture failed. */
  private readonly unphotographed = signal<string | null>(null);

  protected readonly shot = computed(() => {
    const url = this.product().url;
    if (!this.screenshots() || !url || url === this.unphotographed()) {
      return null;
    }
    return screenshotUrl(url);
  });

  protected readonly shotLabel = computed(
    () => `Screenshot of the page at ${this.host() ?? this.product().url}`,
  );

  /** Drop the frame rather than show a broken image. */
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
