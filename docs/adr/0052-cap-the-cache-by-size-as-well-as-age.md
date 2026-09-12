# ADR-0052: Cap the cache by size as well as age

- **Status:** Accepted
- **Date:** 2026-09-12

## Context

`cache.DiskCache` held two bounds' worth of responsibility and implemented one.
`prune` deleted what had expired, and nothing ever deleted an entry that had not:
an entry inside its time to live was kept however many of them there were.

Age is no bound on size, and three things multiply here:

- `cache_ttl` is a setting, and `config.LIMITS` allows thirty days of it -- which
  is the right ceiling for "is this price still evidence" (ADR-0040) and exactly
  the wrong one for a directory.
- A stored page is the whole *visible text* of the page, not the condensed
  excerpt the prompt saw. That is deliberate, so moving `page_chars` does not
  replay a stale excerpt, and it means an entry is tens of kilobytes rather than
  the 1200 characters a run reads.
- A run stores ten pages and an answer per question asked, and the shopping that
  makes this cache worth having is the shopping that fills it.

So a month of daily searching was a month of pages, on a disk nobody was watching,
under a directory the platform calls disposable and nothing was disposing of.

## Decision

`cache.MAX_BYTES` is how much one kind of entry may take up -- 256 MB, per
directory -- and `prune` enforces it after the expiry sweep, deleting the oldest
entries first until what is left fits. The size is a constructor argument
(`DiskCache(..., max_bytes=...)`) so a test can set a small one, and a module
constant rather than a setting: how much of the server's disk this may use is not
a browser's to choose, for the reason `$BUY_AGENT_CACHE_DIR` has no form field.

Expiry is asked first and the cap second, because expiry is free: an entry nobody
may read again is no reason to delete one somebody may. Oldest first, because
every entry that survives the cutoff is still readable and the only thing left to
choose between them by is which run is least likely to ask again -- read off the
same modification time the expiry reads, so the cache has one clock and not two.

## Consequences

- The directory is bounded by something other than how often anybody shops. At
  256 MB that is thousands of pages: the cap exists to have an upper bound, not
  to make a run choose between pages, and a shopper who notices it is a shopper
  whose cache was going to be a gigabyte.
- `prune` answers one number for both sweeps, which is one sentence: how many
  entries the directory no longer holds. A caller wanting them separately would
  be asking a question nothing has.
- The eviction inherits the module's promise that nothing here raises. Every
  candidate refusing to go leaves the directory over its cap and the run carries
  on -- `test_a_cache_that_cannot_be_tidied_is_not_a_failure`.
- It costs one `stat` per live entry, which `prune` was already making, and the
  size is taken from that same answer: asking twice is two answers about a file
  another run may be replacing, and a budget that does not add up.
- A future entry kind added to `cache.py` gets this cap for free and the same
  number, the way it gets one `cache_ttl`. An entry kind that wanted its own would
  be the thing to argue for.
