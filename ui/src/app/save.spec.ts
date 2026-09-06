import { afterEach, beforeEach, vi } from 'vitest';

import { filename, saveText } from './save';

interface Save {
  blobs: Blob[];
  revoked: string[];
  links: HTMLAnchorElement[];
  /** Whether the anchor was in the document at the moment it was clicked --
   *  which is the difference between a download and a button that does nothing. */
  adopted: boolean[];
}

function intercept(): Save {
  const save: Save = { blobs: [], revoked: [], links: [], adopted: [] };
  vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
    save.blobs.push(blob as Blob);
    return 'blob:saved';
  });
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation((href) => void save.revoked.push(href));
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    save.links.push(this);
    save.adopted.push(this.isConnected);
  });
  return save;
}

describe('saveText', () => {
  beforeEach(() => vi.useFakeTimers());

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('hands the text over as a named file', async () => {
    const save = intercept();

    saveText('results.json', '[]', 'application/json');

    expect(await save.blobs[0].text()).toBe('[]');
    expect(save.blobs[0].type).toBe('application/json;charset=utf-8');
    expect(save.links[0].download).toBe('results.json');
  });

  it('clicks a link the document has adopted, and leaves nothing behind', () => {
    /* Some browsers ignore a click on an anchor no document holds, and a
       download that never starts looks exactly like a dead button. */
    const save = intercept();

    saveText('results.json', '[]', 'application/json');

    expect(save.adopted).toEqual([true]);
    expect(save.links[0].isConnected).toBe(false);
    expect(document.querySelectorAll('a[download]')).toHaveLength(0);
  });

  it('keeps the URL until after the click, then gives it back', () => {
    /* Revoked in the same tick, the transfer the click has only just asked for
       can be cancelled -- and never revoked, a page that saves all afternoon
       carries every blob it made until it is reloaded. */
    const save = intercept();

    saveText('results.json', '[]', 'application/json');
    expect(save.revoked).toEqual([]);

    vi.advanceTimersByTime(10_000);
    expect(save.revoked).toEqual(['blob:saved']);
  });
});

describe('filename', () => {
  it('sorts by when the file was taken and says what it is', () => {
    const when = new Date('2026-08-25T14:03:11.500Z');

    expect(filename('log', 'txt', when)).toBe('buy-agent-log-20260825-140311.txt');
    expect(filename('results', 'json', when)).toBe('buy-agent-results-20260825-140311.json');
  });
});
