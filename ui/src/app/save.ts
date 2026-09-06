/**
 * Handing a file to the browser, for the two things a run leaves behind.
 *
 * A failed run leaves a log to attach to a bug report; a finished one leaves
 * results the next question would otherwise throw away. Both are text the page
 * is already holding, and neither is worth a round trip -- so both are saved the
 * same way, in one place rather than once per button.
 */
export function saveText(filename: string, body: string, type: string): void {
  const href = URL.createObjectURL(new Blob([body], { type: `${type};charset=utf-8` }));
  const link = document.createElement('a');
  link.href = href;
  link.download = filename;
  link.hidden = true;
  // In the document for the click and out again after it. A click on an anchor
  // no document has adopted is ignored outright by some browsers, and a download
  // that never starts looks exactly like a button that does nothing -- on the two
  // buttons that exist for the runs which left nothing else behind.
  document.body.append(link);
  link.click();
  link.remove();
  // And the URL let go on a later turn, not this one. Revoking it in the same
  // tick as the click can cancel the transfer the click has only just asked for,
  // which is the same silent nothing by another route. Held meanwhile: the blob
  // is the file, and dropping it is what keeps a page that saves all afternoon
  // from carrying every one of them until it is reloaded.
  setTimeout(() => URL.revokeObjectURL(href), RELEASE_AFTER_MS);
}

/** How long the blob is left reachable after the click. Long enough for any
 *  browser to have started reading it, short enough that nothing accumulates. */
const RELEASE_AFTER_MS = 10_000;

/**
 * A name that sorts by when the file was taken, and survives every filesystem.
 *
 * `what` is what the file is -- `log`, `results` -- so the two land side by side
 * in a downloads folder with the run they came from readable off the stamp.
 */
export function filename(what: string, extension: string, when: Date): string {
  const stamp = when.toISOString().slice(0, 19).replace(/[-:]/g, '').replace('T', '-');
  return `buy-agent-${what}-${stamp}.${extension}`;
}
