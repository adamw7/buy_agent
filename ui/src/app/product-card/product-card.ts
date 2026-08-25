import { Component, computed, input } from '@angular/core';

import type { RankedProduct } from '../agent.types';

/**
 * One ranked product.
 *
 * Every figure shown here came out of `verification.ground()`, which blanks
 * anything the source pages did not actually say -- so an unknown price is shown
 * as unknown rather than quietly left out. The link comes from there too: it is
 * the page the product was found on, never one the model named.
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
