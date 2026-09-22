# ADR-0065: Photograph each product's page, from a server bound to this machine

- **Status:** Accepted
- **Date:** 2026-09-22

## Context

A card says what the run read off a page -- the name, the figures, the quotes -- and
links to it. What it could not say is what the page *looks like*, which is the first
thing a shopper checks once they click: whether it is a shop or a roundup, whether it
is the product they meant, whether the site is one they would buy from. The request
was a picture of the page on the right of each result, linking to it.

Nothing in the package could draw a page. `fetch.py` reads text through `httpx` and
keeps the lines worth a prompt; there is no layout, no image and no script anywhere in
it. Three ways to get a picture were weighed, and each broke a rule this project
already holds:

- **A public screenshot service**, an `<img>` pointing at somebody else's renderer.
  The simplest, and it sends every page a shopper looked at to a third party -- in a
  project built on a local model precisely so the shopping stays on the machine. The
  CSP is `img-src 'self' data:` (`server._SECURITY_HEADERS`), so it also
  means opening the policy to that origin. The fabricated pages under `demo/` would
  render as nothing at all.
- **The page's own preview image**, the `og:image` a link preview uses. No new
  dependency, the HTML is already being read -- and it is the product photograph or a
  logo rather than a picture of the page, which is not what was asked for. It still
  lives on another origin.
- **A real browser**, rendering the page and photographing it. What was asked for,
  and the heaviest: a library, a browser binary of about a hundred megabytes, and a
  process the package starts itself, which `test_the_package_starts_no_process` exists
  to prevent.

The browser was chosen, and the rest of this record is about the shape that keeps it
from costing the things the other rules protect. Three constraints decided that shape.

**A run's products travel back to the server.** A re-sort (ADR-0035) and a payment
(ADR-0046) are both handed the products by the browser, in a body capped at 64 KB.
Pictures in the run payload -- a data URI per product -- would put ten 30 KB JPEGs in
every re-sort, and in the `--json` file and the Download results file that share
`api.results_payload`, and would make every run wait for ten page loads the CLI has no
way to show.

**A browser that draws anything will draw anything.** Given an address, it will render
the router's admin page or a service behind the firewall as readily as a shop, and a
picture is that page's contents handed back. The admission checks (ADR-0018) keep out
a page on another site; nothing keeps out a program on the network asking for itself.

**Playwright's synchronous objects belong to the thread that made them**, and the
server answers every request on a thread of its own (ADR-0010).

## Decision

**A picture is taken with Playwright's headless Chromium, in `buy_agent/screenshots.py`,
and nowhere else.** It is the only module that imports `playwright`, deferred into the
one function that asks whether it can, so a checkout without it imports every module
and passes its suite. The library is an install of its own --
`requirements-screenshots.txt`, then `python -m playwright install --only-shell
chromium` for the browser, which `screenshots.INSTALL` spells out and every refusal
quotes. A page is laid out in a 1280 x 800 window and taken at half scale, a 640 x 400
JPEG at quality 70: the desktop layout a shopper would see, twice the size the card
draws, about 30 KB. It waits for the document, then up to three seconds more for the
page to finish loading, then photographs whatever is drawn. A page that answers 400 or
above is not photographed, a shop's "access denied" beside its product saying the link
is broken when it opens fine in the shopper's own browser.

**The picture is asked for by the card, not taken by the run.** `GET
/api/screenshot?url=` answers with the JPEG, and the card's `<img loading="lazy">` is
what asks, so nothing in the pipeline, the run payload, the journal or `--json` changes,
the CLI pays nothing, and the cards nobody has scrolled to -- the ones under "more the
agent found" -- ask for nothing until they are opened. It runs no pipeline, the way a
re-sort runs none. It answers with `Cache-Control: private, max-age=3600`, so the next
run of the same search draws its cards at once. Only `http` and `https` addresses are
photographed; anything else is a 400 before the browser is asked. A page that will not
be photographed is a 502 -- the request was fine -- and the card drops the frame rather
than drawing a broken image, the title still linking where the picture would have.

**One browser, on one thread, taking one picture at a time.** `screenshots.Camera` owns
a worker thread that launches Chromium on the first picture anybody asks for, takes each
job in the order it arrived -- which is the order the cards are drawn in, the top of the
report first -- and closes the browser once nobody has asked for `IDLE_SECONDS`. Every
request thread queues a job and waits on it, for up to `WAIT_SECONDS`. A launch that
fails is tried again for the next picture, the remedy being a command somebody runs while
the server is up; a browser that fails in a way nothing expected is let go and a fresh one
launched.

**Only a server bound to this machine has a camera.** `server.camera_for` answers `None`
where Playwright is not installed, saying which command would add it, and `None` for any
bind outside `_LOOPBACK_HOSTS`, saying why, since there it would photograph any address
for whoever can reach the port. `create_server(camera=...)` is the seam, `None` by
default, and the server closes the camera with its socket.

**The server says whether it takes pictures, and the page asks only one that does.**
`defaults_payload` carries `screenshots`, a boolean that is not a setting -- a server has
a camera or it has not, and no door can ask for one -- and the card draws a frame only
where it is true and the product links somewhere.

## Consequences

The package now starts a process, and the rule that said it never did is kept as a rule
with one named exception rather than dropped. `test_the_package_starts_no_process` still
holds every module to no `subprocess`, `multiprocessing` or `webbrowser`, and says in its
docstring that the one browser the package starts is Playwright's, for `screenshots.py`
alone. `test_only_screenshots_imports_playwright` is what keeps it to that module, and
`test_the_browser_seam_knows_nothing_about_this_package` keeps the module an address in
and a JPEG out. The threads that wait are four now:
`test_nothing_here_awaits_and_the_threads_are_the_four_that_wait` adds `screenshots.py`
beside `fetch`, `providers` and `server`. Playwright runs an event loop of its own under
its synchronous API, inside that one thread, and nothing in the package awaits it -- the
rule against `asyncio` is about this package's own code and still holds.

The optional install is optional everywhere an optional install has to be.
`.pylintrc`'s `ignored-modules` and `setup.cfg`'s `[mypy-playwright.*]` name it, held
together by `test_mypy_and_pylint_name_the_same_unreadable_libraries`, so a checkout
without it lints and type-checks clean; where it is installed mypy reads its `py.typed`
like any other library's. `requirements-screenshots.txt` is audited nightly by
`audit.yml`, counted as optional by
`test_every_runtime_dependency_is_one_the_package_imports`, named in mutmut's
`also_copy` because that test opens it, and kept out of the image by `.dockerignore`. The
image would never use it: a container binds every interface, and so has no camera.

No test starts a browser, and nothing on a schedule does either. The camera is handed a
stand-in through `launch`, `Chromium` is handed a `playwright` module of the suite's own
through `sys.modules`, and the server tests hand `create_server` a `Photographer`. That
leaves the real library exercised by nobody but the person running it -- the gap
ADR-0028 names for vLLM, one seam over. A Playwright release that changes what `goto`
answers, or what its errors are called, shows up as frames that never fill rather than
as a red run. `ci.yml` installs nothing for this, since nothing in either suite needs it.

The pictures are what a headless browser is shown, which is not always what a person is:
a cookie banner across the top of most European shops, and no frame at all for a site
that turns headless browsers away. Both are honest pictures of what the address answers,
and the title's link is the page itself either way.

The CSP is unchanged -- the picture is same-origin -- and the endpoint is admitted like
every other `GET`, because it sits under `do_GET`'s `_admits` check;
`test_a_picture_is_guarded_like_every_other_request` says so. An `<img>` waiting on a
picture holds one of the browser's six connections to this server for as long as the
picture takes, which lazy loading keeps to the cards on screen; a page that asked for all
ten at once would stall the re-sort behind them.

The card's stylesheet sits just under the 4 kB warning budget `ui/angular.json` sets for a
component's styles, which is why the frame is plain: a picture of the window's own shape,
in a column that exists only when there is a picture in it. A further rule for the card
is likely to be the one that crosses it, and the budget is the thing to argue with then,
not the rules already there.
