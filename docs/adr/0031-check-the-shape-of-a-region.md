# ADR-0031: Check a region's shape, and name it when a search finds nothing

- **Status:** Accepted
- **Date:** 2026-08-31

## Context

`--region` was the last setting that took anything at all. It was free text on
the CLI, `_as_text` in `api.parse_options`, and a box with a `us-en` placeholder
in the form, and whatever it held went straight to
`DDGS().text(..., region=region)`.

That made it the one setting where a mistake fails *quietly*. Every other one is
held to something: `--provider` and `--sort-by` are closed sets read off the
things that implement them, the numbers are held to `config.LIMITS` by both
doors, and `--source` is checked for shape by `parse_sources`. A region is not
guessable -- it is `us-en` and not `en-us`, `uk-en` for the United Kingdom and
not `gb-en` -- so `us_en`, `en-us` and `pl` are all plausible to type. Each of
them ends the run the same way: the backend answers with nothing, the agent logs
"Search returned nothing", and the shopper reads that as the web having nothing
to say about what they want to buy. Nothing anywhere mentioned the region.

Worse than nothing, in two of those cases. Most `ddgs` engines do
`country, lang = region.lower().split("-")`, so `us_en` and `pl` do not name a
region the engine dislikes -- they break it while it is building the query.
Google is handed the halves as they were written, so `US-EN` asks it for results
in a language it spells `lang_EN`, and gets none.

Two shapes of answer were considered before the one taken.

**A closed list of region codes**, turning the field into a `<select>` and the
flag into `choices`, the way `--sort-by` is read off `SortBy`. This is the
strongest check and it was rejected on a fact about `ddgs`: it is no longer a
DuckDuckGo client. It puts the query to Google, Bing, Brave, Mojeek, Startpage
and Wikipedia as well, and each reads the two halves its own way -- Google
builds `hl`, `lr` and `cr` out of them, Wikipedia uses the language half as a
hostname. DuckDuckGo's own list of about sixty codes is therefore not the set of
values that work; a `<select>` built from it would refuse `de-de`, which Google
takes. A list this project maintained by hand would be a rule about this project
rather than about the search, and the failure it caused -- a refusal to search
somewhere the backend would have searched -- is not visibly better than the one
it fixes. `ddgs` 9.15.0 ships no such list to read one off.

**Nothing but a better warning.** Cheapest, and it leaves `--region us_en` a
minute of searching, fetching and extracting before a message that only
*suspects* the region.

## Decision

The region is held to a **shape** rather than to a list, and the shape is
declared once, in `config.py`, beside the field it constrains and beside
`LIMITS`, which is there for the same reason:

```python
REGION = re.compile(r"[a-z]{2}-[a-z]{2,3}")
```

A country and then a language, hyphenated. Three letters on the language half
because `hk-tzh` and `tw-tzh` are real codes, and a shape that refused them
would be about this project rather than about the search.

`config.parse_region` is the one place that check happens. It strips, lower-cases
-- so `US-EN` is a working region rather than a query Google reads as nothing --
and raises `ValueError` naming the shape and three codes that have it. Three
callers use it and no one re-implements it:

- `AgentConfig.__post_init__`, so a Python caller and every front end get the
  same answer, and get it when the config is built rather than a minute into a
  run that has already searched;
- `__main__._region` as argparse's `type`, so a bad one is a usage error
  carrying the shape -- argparse throws a type function's `ValueError` away, as
  it does for `--source`, so the message has to be an `ArgumentTypeError`;
- `api._as_region`, so the browser gets a 400 with the same sentence.

The shape is not the whole story, and the second half of this decision is the
part that covers what it cannot. `en-us` is the right shape the wrong way round;
`zz-zz` is a country nobody serves. Both pass, and both come back empty. So
`BuyAgent._region_note` names the region in the "Search returned nothing"
warning and says what a region does when the backend does not know it -- unless
the region is `config.DEFAULT_REGION`, which is left unnamed, since it is the
one value known to work and pointing at it would send a shopper to correct a
setting that is correct.

The form's field stays a text box with a hint under it naming the shape. It is
not a `<select>`, for the same reason the flag is not `choices`; and it does not
re-check the shape in TypeScript, because the browser decides nothing (ADR-0012)
and a regular expression written on both sides of the language boundary is the
drift `tests/test_conventions.py` exists to catch. A bad region reaches the API,
which answers 400 before the run starts, and the stream reports it as a
`failure` event the way it reports every other refused option.

## Consequences

**A typo is now a usage error, and reads like one.** `--region us_en` exits 2
before anything is searched, with a message naming the shape and `us-en`,
`uk-en`, `pl-pl`. The API answers 400 with the same sentence.

**Two doors have to agree, and a test says so.**
`test_both_front_doors_refuse_the_same_regions` in `tests/test_conventions.py`
is the region's version of the rule `LIMITS` carries for the numbers: a shape
checked on one door only is a CLI that searches on what the API refuses.

**A valid-looking region that no engine serves still returns nothing.** That is
the residue this decision accepts, and the warning is what it is traded for:
the run ends with the region named and with what an unknown one does. A shopper
whose `en-us` found nothing is told where to look; they are not told they were
wrong, because on the shape alone nothing here knows that.

**The default region is deliberately never blamed.** `_region_note` returns
nothing for `DEFAULT_REGION`, so the ordinary empty search reads as it always
did. A future change to the default has to move `DEFAULT_REGION` rather than the
field, or the warning starts naming the value it means to exonerate.

**A three-letter country would be refused.** None exists in these codes today.
If one turns up, the fix is `REGION` and nothing else -- which is the point of
declaring it once.

**The region is now lower-cased on the way in.** `AgentConfig.region` is not
necessarily the string it was constructed with, the way `model` and `base_url`
are not necessarily empty after `__post_init__` resolved them (ADR-0012).
