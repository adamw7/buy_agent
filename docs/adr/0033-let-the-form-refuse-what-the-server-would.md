# ADR-0033: Let the form refuse a value the server would, and mark the field

- **Status:** Accepted
- **Date:** 2026-08-31

## Context

Every setting the web form can hold is one the server already knows how to
judge. The numbers are held to `config.LIMITS` by both front doors, a region is
held to a shape (ADR-0031), and a trusted source is read by
`sources.parse_sources`. None of that was consulted until a run had started.

`SearchForm.canSubmit` checked one thing: that the request box was not empty.
The number fields carried `min` and `max` attributes, which a browser uses for
the spinner arrows and for `:invalid` styling and for nothing else -- nothing on
the page read the form's validity. So `results` of 100, or a Trusted sources
field reading `Marques Brownlee`, opened the event stream, started a run and
came back as a `failure` event: an error banner, next to a progress panel that
had already drawn itself, for a mistake that needed no model, no network and no
minute of waiting to find.

The CLI is the opposite, and had been since sources were added. `--source` is
checked by an argparse `type` function, so a bad one is a usage error carrying
the shapes that work, printed before anything runs. `--results 0` is the same.
Two doors onto one pipeline, and only one of them says no early.

Two further things were wrong with the late refusal. The message named the field
-- `results must be between 1 and 50; got 51.` -- but arrived in the same banner
a model server being down arrives in, with the settings panel very possibly
closed over the box it was about. And the `min`/`max` in the template were a
second copy of `config.LIMITS`, written in a file no Python test reads: the two
could drift apart with both halves staying perfectly valid.

The obvious shape -- re-implement the checks in TypeScript -- is the one
ADR-0031 already refused for the region, and for the reason that outlives any
one setting: a rule written on both sides of the language boundary is drift that
neither suite can see, and the browser decides nothing (ADR-0012).

## Decision

The form refuses what the server would, **before** opening a run, without
holding any rule of its own. Three parts, and the third is what keeps the second
line from becoming a second opinion.

**The ranges are shipped.** `GET /api/config` now carries `limits`, built by
`api.limits_payload` off `config.LIMITS` through `api._BOUNDED` -- the one table
saying which config field bounds each request key. The form binds `[min]` and
`[max]` from it, holds each number to it, and says `Between 1 and 50.` under the
field that is outside. It applies the range; it does not choose it, and there is
no second copy to go stale.

**The sources are asked about.** A source is not a range -- it is a scheme taken
off the front, a hostname matched, routing segments dropped -- so the browser
asks: `GET /api/sources?sources=...` reads the field with the same
`parse_sources` a run would use and answers `{"sources", "error"}`, with `error`
empty for a field with nothing wrong with it. 200 either way, because "does this
parse" is a question that was answered. The answer names the spec it was about,
so one that arrives for text the shopper has since typed over is dropped rather
than shown against what replaced it. The form asks when the field is left rather
than on every keystroke: half a spec is not a mistake, `rtings.co` is a site on
the way to `rtings.com`, and clicking **Find products** leaves the field first.
A remembered value is asked about too, when the form seeds itself -- nobody is
about to type it, so nothing else would ever ask.

**A refusal names its field.** `ApiError` carries `field`, the request key the
unusable value arrived under, and `payload()` sends it -- so the stream's
`failure` event does, and the page marks that input as well as showing the
banner. This is the half that covers what the form cannot judge: a region of the
wrong shape, a source submitted before the check for it answered, a `sort_by`
nothing offers. Which box a sentence belongs under is decided in Python, the way
the wording of an unknown price is; the browser reads a key, not a message.

The server's checks are untouched. This is an earlier line, not a replacement:
`parse_options` still refuses everything it refused before, with the same
sentences and the same statuses, for the CLI-shaped clients that never see a
form and for a browser that got past the form.

## Consequences

**A bad setting costs nothing now.** 51 products, or a source naming a person,
marks the field and disables the button. No stream, no run, no banner.

**The form is only ever as strict as the server.** Every mark it makes on its
own comes from a range the server sent or a sentence the server wrote. There is
one wording in TypeScript -- `Between 1 and 50.` -- and it is composed out of
the two numbers that were shipped.

**Three new agreements, and `tests/test_conventions.py` holds them.** The form's
table of number fields must match the keys `limits_payload` ships; the template
must carry no written `min` or `max`; and every key `parse_options` reads must
be one the form sends, or a refusal names a box that is not there.

**An endpoint that runs nothing.** `GET /api/sources` is the first one that
neither starts a search nor reaches a model server, which is what makes it cheap
enough to call while a form is being filled in.

**A field checked on leaving, not on typing.** A shopper who types a bad source
and submits without leaving the field still gets the late refusal -- now marked
on the field rather than only bannered. Debouncing every keystroke was the
alternative and was not worth a timer in the component and a request per
character.

**`num_ctx` keeps its blank.** A cleared number field means "use the default"
(ADR-0012) and is not held to any range, here or on the server. That is what
makes the context box's placeholder honest.

**A region is still refused late.** Its shape stays in Python (ADR-0031) and is
not shipped as a pattern to apply here, because a regular expression on both
sides is exactly the drift that decision refused. What it gains is the mark: the
run is refused as it always was, and the box is now the one wearing the message.
