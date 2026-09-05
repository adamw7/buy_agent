import { TestBed } from '@angular/core/testing';

import { ProductCard } from './product-card';
import type { RankedProduct } from '../agent.types';

const SONY: RankedProduct = {
  rank: 1,
  score: 0.912,
  breakdown: {
    rating: 0.94,
    popularity: 1,
    price: 0.32,
    total: 0.912,
    neutral: [],
  },
  name: 'Sony WH-1000XM5',
  price: 328,
  currency: 'USD',
  rating: 4.7,
  review_count: 12000,
  seller: 'Amazon',
  url: 'https://www.example.com/sony',
  notes: 'Best noise cancelling.',
  opinions: ['the noise cancelling is uncanny', 'the case is bulky'],
  price_label: '328.00 USD',
  rating_label: '4.7/5 (12,000 reviews)',
};

const UNKNOWN: RankedProduct = {
  ...SONY,
  rank: 4,
  score: 0.5,
  breakdown: {
    rating: 0.5,
    popularity: 0.5,
    price: 0.5,
    total: 0.5,
    neutral: ['rating', 'popularity', 'price'],
  },
  name: 'Anker Q30',
  price: null,
  rating: null,
  seller: null,
  url: null,
  notes: null,
  opinions: [],
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

  it('survives an unparseable link rather than showing a broken host', async () => {
    /* The url is a search result's own, so nothing guarantees it parses. */
    const card = await render({ ...SONY, url: 'wherever you buy headphones' });

    expect(card.querySelectorAll('.pill').length).toBe(2); // rating and seller, no host
    expect(card.textContent).toContain('Sony WH-1000XM5');
  });

  it('is still readable when there is no link to give', async () => {
    const card = await render(UNKNOWN);
    expect(card.querySelector('h3 a')).toBeNull();
    expect(card.querySelector('h3')!.textContent).toContain('Anker Q30');
  });

  it('draws the score as a share of one, and says what the share is of', async () => {
    /* A bare `91` beside a bar is the one figure on the card with no unit. */
    const card = await render(SONY);
    const score = card.querySelector('.score')!;

    expect(score.textContent).toContain('Score');
    expect(score.querySelector('.value')!.textContent).toContain('91%');
    expect(card.querySelector<HTMLElement>('.fill')!.style.inlineSize).toBe('91%');
  });

  it('gives the bar a name and a value a screen reader can read', async () => {
    /* Without these the bar is a nameless div and the number is all there is. */
    const meter = (await render(SONY)).querySelector('[role="meter"]')!;

    expect(meter.getAttribute('aria-label')).toBe('Score');
    expect(meter.getAttribute('aria-valuenow')).toBe('91');
    expect(meter.getAttribute('aria-valuetext')).toBe('91%');
    expect(meter.getAttribute('aria-valuemin')).toBe('0');
    expect(meter.getAttribute('aria-valuemax')).toBe('100');
  });

  it('says what the score is made of, so the bar is not the whole story', async () => {
    /* A bare 91% says where a product placed and nothing about why (ADR-0041). */
    const shares = [...(await render(SONY)).querySelectorAll('.parts li')];

    expect(shares.map((share) => share.querySelector('.part')!.textContent)).toEqual([
      'rating',
      'popularity',
      'price',
    ]);
    expect(shares.map((share) => share.querySelector('.share')!.textContent)).toEqual([
      '94%',
      '100%',
      '32%',
    ]);
  });

  it('marks the shares nobody published rather than passing them off as read', async () => {
    /* The distinction the breakdown exists for: 0.5 because the page said
       nothing looks exactly like 0.5 because the product is middling. */
    const shares = [...(await render(UNKNOWN)).querySelectorAll('.parts li')];

    expect(shares.every((share) => share.classList.contains('assumed'))).toBe(true);
    expect(shares[0].textContent).toContain('assumed');
  });

  it('takes which shares were assumed from the payload, never from the value', async () => {
    /* A product priced exactly mid-way through the set scores 0.5 on price
       having been read off a page. Worked out here from the number, the card
       would call that one assumed and libel it. */
    const middling = {
      ...SONY,
      breakdown: { rating: 0.5, popularity: 0.5, price: 0.5, total: 0.5, neutral: [] },
    };

    const shares = [...(await render(middling)).querySelectorAll('.parts li')];

    expect(shares.some((share) => share.classList.contains('assumed'))).toBe(false);
    expect((await render(middling)).textContent).not.toContain('assumed');
  });

  it('quotes what the sources said about it, one line each', async () => {
    /* Every quote here survived grounding, so it is somebody's actual words. */
    const quotes = (await render(SONY)).querySelectorAll('.opinions li');

    expect([...quotes].map((quote) => quote.textContent!.trim())).toEqual([
      'the noise cancelling is uncanny',
      'the case is bulky',
    ]);
  });

  it('shows nothing at all where the sources gave no opinion', async () => {
    /* An empty quote block would read as a page that said nothing good. */
    const card = await render(UNKNOWN);

    expect(card.querySelector('.opinions')).toBeNull();
  });

  it('marks the ones that made the top of the report', async () => {
    expect((await render(SONY, true)).classList).toContain('highlighted');
    expect((await render(SONY, false)).classList).not.toContain('highlighted');
  });
});
