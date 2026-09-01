import { afterEach, vi } from 'vitest';

import { filename, saveText } from './save';

describe('saveText', () => {
  afterEach(() => vi.restoreAllMocks());

  it('hands the text over as a named file and gives the URL back', async () => {
    const blobs: Blob[] = [];
    const revoked: string[] = [];
    const links: HTMLAnchorElement[] = [];
    vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
      blobs.push(blob as Blob);
      return 'blob:saved';
    });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation((href) => void revoked.push(href));
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      links.push(this);
    });

    saveText('results.json', '[]', 'application/json');

    expect(await blobs[0].text()).toBe('[]');
    expect(blobs[0].type).toBe('application/json;charset=utf-8');
    expect(links[0].download).toBe('results.json');
    // A page that keeps making these leaks every one until it is reloaded.
    expect(revoked).toEqual(['blob:saved']);
  });
});

describe('filename', () => {
  it('sorts by when the file was taken and says what it is', () => {
    const when = new Date('2026-08-25T14:03:11.500Z');

    expect(filename('log', 'txt', when)).toBe('buy-agent-log-20260825-140311.txt');
    expect(filename('results', 'json', when)).toBe('buy-agent-results-20260825-140311.json');
  });
});
