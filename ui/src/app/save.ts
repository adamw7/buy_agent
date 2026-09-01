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
  link.click();
  URL.revokeObjectURL(href);
}

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
