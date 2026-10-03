# The recorded UI demos

Three runs of the UI and one of the benchmark's page, recorded in Chromium at
1280x720 and 25fps as MPEG program streams, plus a local counterparty for the
`http` payment rail (see the end).

| Video | The shopper asks for | Ends on | Sound |
| --- | --- | --- | --- |
| `wwii-books-1944-45.mpg` | *"wwii books about war in Europe 1944-45"* | the top 3, with the rest folded away | no |
| `wwii-books-1944-45-with-sound.mpg` | the same | the same | yes |
| `laptops-under-1000.mpg` | *"new laptop below 1000 USD, not too heavy or loud. windows 11 installed"* | the shop page behind the top product's link | no |

Each of the three has a script, `books.py` or `laptops.py`, holding the ten pages it
searches and the fake model's answer. `--script` picks one; a third demo is a
module offering the same five names plus a row in `server.SCRIPTS`.

`benchmark-on-cpu.mpg` (4 min 23 s, narrated) is the other kind: the benchmark's
page scoring a real `qwen3:0.6b` in Ollama, on four CPU cores and no GPU,
against the two reference answers over all three cases. It ends on what
`ollama ps` reported throughout. See [The benchmark, on a CPU](#the-benchmark-on-a-cpu).
It was taken with Ollama 0.35.1, and its `qwen3:0.6b` is unsloth's Qwen3-0.6B
Q4_K_M GGUF (397 MB) from Docker Hub's `ai/qwen3`, imported with `ollama create`:
the same model as Ollama's own tag, quantised by unsloth rather than by Ollama.

## What is real in it

Everything between the search and the ranking: the real `fetch.condense`,
`clean_products`, `ground`, `deduplicate` and `rank_products`. Both scripts make
the fake model wrong in six ways, and the panel shows each being caught:

| Log line | What was wrong |
| --- | --- |
| `Discarded 1 result(s) that were pages, not products` | A listicle headline reported as a product |
| `Dropped 1 product(s) absent from the search results` | A name no page mentions |
| `Dropped unsupported figures on 1 product(s)` | A price no page printed -- the card reads "price unknown" |
| `Dropped 1 opinion(s) the sources never printed` | A verdict nobody wrote |
| `Dropped 1 link(s) to pages that were never searched` | A link to a page the agent never saw |
| `Merged 1 duplicate listing(s)` | One product listed twice, in two currencies |

What is not real: `search_web` and `enrich` return the script's pages, the chat
model is scripted, and `GET /api/models` answers from a list. The book titles,
authors and laptop models are real; shops, prices, ratings, reviews and quotes
are invented, on `*.example` hosts that cannot resolve.

## Why MPEG-2 and not MPEG-1

1280x720 is outside MPEG-1's constrained parameters, so its streams violate the
system target decoder. Silent players cope; one scheduling an audio track opens
nothing. `VIDEO` in `recording.mjs`, which both recorders use, states the rate and
buffer and uses MPEG-2. The two silent recordings predate it; a new take of either
is MPEG-2 too.

## The soundtrack

Chromium records no audio. `record.mjs` writes a *cue* per event (each key, each
click, each log line, the results landing), and `sound.py` synthesises a WAV of
the video's length from sine waves, muxed in as MP2. A log line that took
something away gets a lower, longer note; `TOOK_SOMETHING_AWAY` matches the verb
(`Discarded`, `Dropped`, `Merged`). `sound.spread` pushes simultaneous lines
apart by `LINE_GAP`, and the mix is limited rather than normalised.

A `say` cue is a spoken line, from `narration.py`. `sound.voice_over` lays it
over the limited mix, which ducks to `DUCK` beneath it, so the voice is never
squashed by the limiter.

## The link at the end

`--follow-link` clicks the top product's name. On the laptops run the fake model
linked that product to a shop never searched, so the page that opens is the one
`attribute_sources` chose instead. `record.mjs` serves `*.example` itself, from
the same page text the pipeline was handed.

## Recording them again

`--pace` scales the scripted delays; at 0.6 no step is silent for more than about
a second.

```powershell
cd ui ; npm install ; npm run build ; cd ..     # server.DEFAULT_UI_DIR wants this

python -m demo.server --script laptops --pace 0.6 --port 8000    # in one terminal
node demo/record.mjs --url http://127.0.0.1:8000 --script laptops --follow-link `
    --out demo/laptops-under-1000.mpg

python -m demo.server --script books --pace 0.6 --port 8000      # the other one
node demo/record.mjs --url http://127.0.0.1:8000 --script books `
    --out demo/wwii-books-1944-45.mpg

python -m demo.server --script books --pace 0.45 --port 8000     # and the third
node demo/record.mjs --url http://127.0.0.1:8000 --script books --sound `
    --out demo/wwii-books-1944-45-with-sound.mpg
```

Pass the same `--script` to both: the server searches that fabricated web, and
the recorder reads the request and shop pages from it. `--request` types
something else. The recorder prints the length and cue count. A budget in the
request stops the first press at the offered bound
([ADR-0059](../docs/adr/0059-notice-a-bound-in-the-request-and-offer-it.md)), so
the recorder presses twice.

`record.mjs` needs Playwright (local or global), Python on PATH, and an ffmpeg
with the `mpeg` muxer and the `mpeg2video` and `mp2` encoders. Playwright's own
ffmpeg has none of those, so a system one is preferred; `--ffmpeg` names another.

## The benchmark, on a CPU

`benchmark.mjs` films the benchmark's page (`python -m benchmark.server`,
[ADR-0070](../docs/adr/0070-compare-local-models-and-give-the-comparison-a-page.md)) comparing
real models served by Ollama with the two reference answers, and talks over it:
what the page is, the server and its models, the references, the cases, the run
as it goes, the standings, one model opened, and how a run is scored. Each line
is captioned on screen as it is said.

```powershell
ollama pull qwen3:0.6b
$env:CUDA_VISIBLE_DEVICES = "-1" ; $env:LLAMA_ARG_NO_REPACK = "1"
ollama serve                                           # one terminal: no GPU
$env:BUY_AGENT_CACHE_DIR = "$env:TEMP\benchmark-demo"   # another: an empty board
python -m benchmark.server
node demo/benchmark.mjs --models qwen3:0.6b --out demo/benchmark-on-cpu.mpg
```

It is a demo of the models running **on the CPU only**, and it checks rather
than asserts it. The benchmark sends no `num_gpu`, so where the layers go is
Ollama's choice; hiding the GPUs from it (`CUDA_VISIBLE_DEVICES=-1`, or
`HIP_VISIBLE_DEVICES=-1` on AMD) is what keeps them off one. On a Xeon with AMX,
llama.cpp also repacks the weights into a buffer for the CPU's matrix units, and
Ollama (0.35) counts every buffer but the host's as GPU memory: `ollama ps`
reads `84%/16% CPU/GPU` on a machine with no GPU at all. `LLAMA_ARG_NO_REPACK=1`
(llama.cpp's `--no-repack`) keeps them in RAM, and cost nothing measurable on
`qwen3:0.6b`. The recorder polls Ollama's `/api/ps` (what `ollama ps` prints)
every second, shows it in a corner, and refuses to write a take in which any
model had memory on a GPU. The last card states what it saw.

- `--models` is a comma-separated list, each one pulled; `--cases` narrows the
  three (`headphones,laptops,espresso`).
- A model on a CPU takes minutes. Where it is only working, with nothing being
  said, the video runs `--fast-forward` times faster (8 by default; 1 never),
  with a badge saying so. The page's own clock keeps real time throughout, and
  the soundtrack is moved with the picture.
- The voice is `narration.py`'s: SVOX Pico (`pico2wave`, in Debian and Ubuntu's
  `libttspico-utils`) if it is installed, else `espeak-ng`. The recorder waits
  for each line to end before the next step, so they never drift apart.
- It is encoded at 1.2 Mbit/s with a keyframe every ten seconds (`video()` in
  `recording.mjs`), not `VIDEO`'s 3 Mbit/s and twelve frames: minutes of a page
  that mostly sits still came to 103 MB that way, over GitHub's limit for a
  file, and to 42 MB this way.
- It refuses a board that already holds runs, since the standings would not be
  this run's; `--clear` forgets them instead. `--url` and `--ollama` move the two
  addresses.

## The README's pictures

`screenshot.mjs` takes `docs/ui.png` (the form with settings open) and, given
`--script`, `docs/results.png` (the top 3, after pressing the button twice where
the request names a budget):

```powershell
python -m demo.server --pace 0 --port 8000        # in one terminal
node demo/screenshot.mjs --out docs/ui.png        # in the other

python -m demo.server --script laptops --pace 0 --port 8000
node demo/screenshot.mjs --script laptops --out docs/results.png
```

It needs `demo.server` for the model dropdown and pill. It clips to the card, so
the picture grows with the form, and renders at twice the CSS width. `--url`,
`--width`, `--scale` and `--request` move the rest.

## A merchant for the `http` rail

`merchant.py` answers the two requests `--rail http` makes. It needs the AP2 SDK
and nothing else.

```powershell
python -m demo.merchant --once        # the whole round trip, in one process
```

`--once` makes a throwaway key, starts the merchant on a free port, buys a
made-up cart through the real `payment.pay_for` and `http` rail, then presents
two authorisations it must refuse (a different amount, an already-paid
checkout), exiting 1 if either is accepted. As a server:

```powershell
$env:BUY_AGENT_AP2_KEY = "agent-key.pem"    # the agent's key; the merchant reads its public half
python -m demo.merchant                     # on 127.0.0.1:8765
python -m buy_agent "headphones" --pay --rail http --merchant-url http://127.0.0.1:8765
```

Without a model, run `python -m demo.server --script laptops` from a shell with
the same key, tick **Offer to pay for what it finds**, choose the HTTP endpoint
rail, give the merchant's address as **Payment endpoint**, and press Pay.

The contract is this project's, not AP2's:

| Request | Body | Answer |
| --- | --- | --- |
| `POST /checkout` | `{"checkout": <the cart as AP2's checkout document>}` | `{"checkout_jwt": <that document, signed by the merchant>, "nonce": <a challenge>}` |
| `POST /payment` | `{"transaction_id", "checkout_mandate", "payment_mandate"}` | `{"paid": true, "detail": ...}`, or `false` and why |

A payment is accepted only when it names an unpresented checkout this merchant
signed, the Checkout Mandate verifies against the agent's key and carries that
checkout and hash, and the Payment Mandate verifies with the checkout's
transaction, amount, currency and payee. Verification uses the AP2 SDK, never
`buy_agent.mandates`. With `--mandate` (or `$BUY_AGENT_AP2_MANDATE`) it expects a
chain from the shopper's open mandate, answering its nonce and inside its
constraints. A decline is `"paid": false`; a malformed request is a 400. Nothing
is ever charged.

Nothing here is imported, collected by pytest, or copied into the image.
