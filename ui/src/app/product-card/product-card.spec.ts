import { TestBed } from '@angular/core/testing';

import { ProductCard } from './product-card';
import type { RankedProduct } from '../agent.types';

const SONY: RankedProduct = {
  rank: 1,
  score: 0.912,
  name: 'Sony WH-1000XM5',
  price: 328,
  currency: 'USD',
  rating: 4.7,
  review_count: 12000,
  seller: 'Amazon',
  url: 'https://www.example.com/sony',
  notes: 'Best noise cancelling.',
  price_label: '328.00 USD',
  rating_label: '4.7/5 (12,000 reviews)',
};

const UNKNOWN: RankedProduct = {
  ...SONY,
  rank: 4,
  score: 0.5,
  name: 'Anker Q30',
  price: null,
  rating: null,
  seller: null,
  url: null,
  notes: null,
  price_label: 'price unknown',
  rating_label: 'unrated',
};

async function render(product: RankedProduct, highlighted = false): Promise<HTMLElement> {
  const fixture = TestBed.createComponent(ProductCard);
  fixture.componentRef.setInput('product', product);
  fixture.componentRef.setInput('highlighted', highlighted);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('ProductCard', () => {
  it('shows the figures the way Python labelled them', async () => {
    const card = await render(SONY);
    expect(card.querySelector('.price')!.textContent).toContain('328.00 USD');
    expect(card.textContent).toContain('4.7/5 (12,000 reviews)');
    expect(card.textContent).toContain('Amazon');
    expect(card.querySelector('.rank')!.textContent).toContain('#1');
  });

  it('says a figure is unknown rather than leaving it out', async () => {
    /* A blanked figure means grounding did not find it in the sources. */
    const card = await render(UNKNOWN);
    expect(card.textContent).toContain('price unknown');
    expect(card.textContent).toContain('unrated');
  });

  it('links to where the product was found, in a new tab', async () => {
    const link = (await render(SONY)).querySelector<HTMLAnchorElement>('h3 a')!;
    expect(link.href).toBe('https://www.example.com/sony');
    expect(link.rel).toContain('noopener');
  });

  it('shows the host, so it is obvious which shop this came from', async () => {
    expect((await render(SONY)).textContent).toContain('example.com');
  });

  it('is still readable when there is no link to give', async () => {
    const card = await render(UNKNOWN);
    expect(card.querySelector('h3 a')).toBeNull();
    expect(card.querySelector('h3')!.textContent).toContain('Anker Q30');
  });

  it('draws the score as a share of one', async () => {
    const card = await render(SONY);
    expect(card.querySelector('.score')!.textContent).toContain('91');
    expect(card.querySelector<HTMLElement>('.fill')!.style.inlineSize).toBe('91%');
  });

  it('marks the ones that made the top of the report', async () => {
    expect((await render(SONY, true)).classList).toContain('highlighted');
    expect((await render(SONY, false)).classList).not.toContain('highlighted');
  });
});
