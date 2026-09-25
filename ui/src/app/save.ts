/** Handing a file to the browser, for the two things a run leaves behind. */
export function saveText(filename: string, body: string, type: string): void {
  const href = URL.createObjectURL(new Blob([body], { type: `${type};charset=utf-8` }));
  const link = document.createElement('a');
  link.href = href;
  link.download = filename;
  link.hidden = true;
  // Attached for the click: some browsers ignore a click on a detached anchor.
  document.body.append(link);
  link.click();
  link.remove();
  // Revoked later: revoking in the same tick can cancel the download.
  setTimeout(() => URL.revokeObjectURL(href), RELEASE_AFTER_MS);
}

/** How long the blob is left reachable after the click. */
const RELEASE_AFTER_MS = 10_000;

/** A name that sorts by when the file was taken, and survives every filesystem. */
export function filename(what: string, extension: string, when: Date): string {
  const stamp = when.toISOString().slice(0, 19).replace(/[-:]/g, '').replace('T', '-');
  return `buy-agent-${what}-${stamp}.${extension}`;
}
