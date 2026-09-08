import { Component, computed, input, output, signal } from '@angular/core';

import type { RailOption, RankedProduct, Receipt, ScoreWeights } from '../agent.types';

/**
 * The criteria a score is blended from, in the order they are weighted.
 *
 * `ranking.CRITERIA` on the Python side, which is a list there for the same
 * reason it is one here: the name is what looks a share up in `ScoreParts` and
 * its weight up in `ScoreWeights`, so writing it beside each value would be the
 * name said twice a row -- and the two halves free to name different criteria.
 */
const CRITERIA = [
  'rating',
  'popularity',
  'price',
] as const satisfies readonly (keyof ScoreWeights)[];

/**
 * One criterion behind the score, as the card draws it.
 *
 * `percent` is that criterion scored on its own out of 100, and `weight` is how
 * much of the blend it was allowed to decide -- both, because either alone
 * misleads. Three bare percentages under a total read as parts of it and are
 * not: they do not add up to the score, and which of them the placing turned on
 * is the weight's to say. `weight` is null before a run has answered, which is
 * the only time the card has products and no weights to draw them with.
 *
 * `assumed` is Python's answer and not a guess made here: it is the name
 * appearing in `breakdown.neutral`, which is how a criterion nothing was
 * published for is told from one that scored middling -- both are 0.5, and
 * only the payload knows which is which (ADR-0041).
 */
interface ScoreShare {
  name: string;
  percent: number;
  weight: number | null;
  assumed: boolean;
}

/**
 * One ranked product.
 *
 * Every figure shown here came out of `verification.ground()`, which blanks
 * anything the source pages did not actually say -- so an unknown price is shown
 * as unknown rather than quietly left out. The link comes from there too: it is
 * the page the product was found on, never one the model named.
 *
 * The opinions are shown as quotes because that is what they are: words a source
 * page printed, which `verification.verify_opinions()` checked are really there
 * -- each with a link to the page that printed it, which is the only way a
 * shopper can check a quote the way a figure is checked by following the
 * product's own link (ADR-0042). Nothing here summarises or scores them, and
 * nothing here decides which page a quote came off -- the browser decides
 * nothing.
 */
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

  /** How much each criterion counted, as the run that produced this reported it.
   *  A fact about the run rather than the product, so it arrives beside the
   *  products rather than inside each one. */
  readonly weights = input<ScoreWeights | null>(null);

  /** Whether this run may pay at all: the shopper asked for it and the server
   *  can. Whether *this product* may be is a separate answer and Python's --
   *  `product().cannot_pay` -- so the button is offered only where both agree. */
  readonly canPay = input(false);

  /** The rail the payment would go through, so the confirmation can say whether
   *  anybody is about to be charged. Python decides that, on the rail's row. */
  readonly rail = input<RailOption | null>(null);

  /** A payment already in flight, anywhere on the page: one at a time. */
  readonly paying = input(false);

  /** What came of paying for *this* product, once something did. */
  readonly receipt = input<Receipt | null>(null);

  /**
   * The approval a person gave, emitted when they confirm.
   *
   * The three fields they were shown and agreed to, which the server holds
   * against the cart it builds itself. It is an echo and not an instruction --
   * the page is the surface that witnessed the consent, not the thing that
   * decides what the consent was worth.
   */
  readonly pay = output<{ title: string; price: number; currency: string }>();

  /**
   * Whether this card is showing its confirmation.
   *
   * The payment is two clicks and the second one is next to the price, because
   * this is the small Trusted Surface AP2 asks for: the place a person is shown
   * exactly what they are agreeing to before anything is signed. A single button
   * would be a purchase made by a misclick on a card in a list.
   */
  protected readonly confirming = signal(false);

  /** Whether to offer the button at all: the run asked, the server can, this
   *  product has a price a source printed, and nothing has been bought yet. */
  protected readonly offersPayment = computed(
    () => this.canPay() && this.product().cannot_pay === null && this.receipt() === null,
  );

  /** Why this one cannot be bought, where the run could have bought something.
   *  Shown rather than swallowed: a card with no button beside cards that have
   *  one is a question, and Python already wrote the answer. */
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
    // `pay_currency` and not `currency`: the money a purchase is in is the
    // run's, so a page that printed a bare "329.00" leaves the product's own
    // `null` while the cart is in USD all the same (ADR-0043). Read off the
    // product, this returned before emitting anything -- a confirm button that
    // did nothing at all, on every product whose page named no currency.
    //
    // Narrowed by `offersPayment`, which is what draws the button: a product
    // with no price has `cannot_pay` set and never gets one.
    if (product.price === null || product.pay_currency === null) {
      return;
    }
    this.confirming.set(false);
    this.pay.emit({ title: product.name, price: product.price, currency: product.pay_currency });
  }

  protected readonly percent = computed(() => Math.round(this.product().score * 100));

  /**
   * The score with its unit on it. The bar alone reads as a bare number, and the
   * score is the one figure on the card that does not say what it is -- a price
   * carries its currency and a rating carries its `/5`. Wording is presentation,
   * so it is written here rather than sent from Python, the same way `shortName`
   * trims a logger name; the raw `score` is on the payload either way.
   */
  protected readonly scoreLabel = computed(() => `${this.percent()}%`);

  /**
   * What the score is made of, in the order the criteria are weighted.
   *
   * Shown because the bar alone says where a product placed and nothing about
   * why -- and because half of these numbers are routinely not measurements at
   * all: a product no page rated scores the same 0.5 as one rated exactly
   * average, and the shopper cannot tell those apart from the total. Each is
   * drawn as a percentage for the reason the total is, and marked where it was
   * assumed. Which of them were assumed is read off the payload, never worked
   * out here from a value equalling 0.5: a genuinely mid-priced product scores
   * exactly that, and the card would libel it.
   *
   * The weight comes with each one because without it the three read as parts of
   * the total: they are each out of 100 on their own, they do not add up to the
   * score, and a product placed on its price alone looks identical to one placed
   * on its rating. Python normalises the weights; nothing here works one out.
   */
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
