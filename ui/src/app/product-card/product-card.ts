import { Component, computed, input } from '@angular/core';

import type { RankedProduct } from '../agent.types';

/**
 * One criterion's share of the score, as the card draws it.
 *
 * `assumed` is Python's answer and not a guess made here: it is the name
 * appearing in `breakdown.neutral`, which is how a criterion nothing was
 * published for is told from one that scored middling -- both are 0.5, and
 * only the payload knows which is which (ADR-0041).
 */
interface ScoreShare {
  name: string;
  percent: number;
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
 * page printed, which `verification.verify_opinions()` checked are really there.
 * Nothing here summarises or scores them -- the browser decides nothing.
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
   * average, and the shopper cannot tell those apart from the total. The share
   * is drawn as a percentage for the reason the total is, and marked where it
   * was assumed. Which of them were assumed is read off the payload, never
   * worked out here from a value equalling 0.5: a genuinely mid-priced product
   * scores exactly that, and the card would libel it.
   */
  protected readonly parts = computed<ScoreShare[]>(() => {
    const breakdown = this.product().breakdown;
    const assumed = new Set(breakdown.neutral);
    const shares: [string, number][] = [
      ['rating', breakdown.rating],
      ['popularity', breakdown.popularity],
      ['price', breakdown.price],
    ];
    return shares.map(([name, share]) => ({
      name,
      percent: Math.round(share * 100),
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
