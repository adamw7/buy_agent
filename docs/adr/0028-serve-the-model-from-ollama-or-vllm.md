# ADR-0028: Serve the model from Ollama or from vLLM, behind one provider seam

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

[ADR-0003](0003-local-ollama-no-api-keys.md) decided that the model is a local
one served by Ollama, reached through `langchain-ollama`, with no accounts and no
API keys. Nothing about that has gone wrong. What has changed is who is asking.

Ollama is the right answer for one machine: pull a tag, run a command, switch
models per request. It is the wrong answer for a machine that already has a vLLM
on it. vLLM is what serves a model to more than one person at a time -- it
batches requests, it is what a shared GPU box in a lab or a team runs, and
someone who already has one is being asked here to install a second model server,
pull a second copy of a model they already have loaded, and give up the GPU
memory to hold both.

That is not a hosted model and it is not a hosted service: a vLLM on the machine
next to yours is the same trade ADR-0003 made -- the request does not leave the
network you control, there is no account, and the only key involved is one the
person who started the server chose. So the reason ADR-0003 gave for local-only
does not argue against this. It argues for it.

The two servers are genuinely different, and a design that pretended otherwise
would lie to the shopper:

- Ollama holds many pulled tags and picks between them per request. A vLLM
  process serves the one model it was started with. So "the model is not there"
  is `ollama pull <tag>` on one and `vllm serve <model>` -- a restart -- on the
  other, and the useful part of that failure is which sentence it prints
  ([ADR-0009](0009-three-failure-modes.md)).
- Ollama takes the context window per request (`num_ctx`). vLLM fixes it when it
  starts, with `--max-model-len`. A `--num-ctx` sent to vLLM is a setting that
  quietly does nothing, which is worse than a setting that is not offered.
- Their transports raise different exceptions. `BuyAgent._invoke` catches a tuple
  to turn a stopped server into `OllamaUnavailableError`; a stopped vLLM raises
  `openai.APIConnectionError`, which is in none of those classes and would reach
  the shopper as a traceback and the browser as a 500.
- Their model names are different kinds of thing: `gemma4:12b` against
  `Qwen/Qwen3-8B`, on different ports. One default pair cannot serve both, and a
  form that carried an Ollama tag over to a vLLM would fail for a reason nothing
  on the page explained.

What is *not* different is everything this project is actually about. Both speak
a chat API that LangChain already wraps as a `BaseChatModel`; both constrain
decoding to a JSON schema, which is what [ADR-0004](0004-json-schema-and-sentinels.md)
rests on; and neither of them is trusted with a judgement anyway, because
grounding, ranking and filtering are Python either way (ADR-0002, ADR-0006).

## Decision

`AgentConfig.provider` chooses the model server -- `"ollama"` (the default, and
still the run the README opens with) or `"vllm"` -- overridable with
`$BUY_AGENT_PROVIDER`. It is a flag (`--provider`), a request option, and a
`<select>` in the form, so all three front ends can reach it.

Everything that differs between the two lives in one new module,
`buy_agent/providers.py`, as a `Provider` record answering four questions: what
chat model this config builds, which exceptions mean the server is not there,
how that failure is phrased, and what the server is currently serving. Nothing
above that module branches on which one is running. `BuyAgent` builds its model
through `build_chat_model(config)` and catches `transport_errors(config)`;
`api.installed_models` asks `list_models(config)`; the CLI, the API and the form
each read the registry rather than listing the providers again.

The model and the address are resolved *from* the provider rather than defaulted
beside it. `AgentConfig.model` and `AgentConfig.base_url` default to the empty
string -- the same "unset" a blank form field means
([ADR-0012](0012-the-browser-decides-nothing.md)) -- and `__post_init__` fills in
the pair `config.PROVIDER_DEFAULTS` holds for that provider. Choosing a provider
and nothing else is therefore a complete choice, on the CLI and in the form
alike; the form fills both fields in when the picker changes, so neither is left
holding the other server's answer.

The one exception is asymmetric on purpose. `Provider.takes_num_ctx` is false for
vLLM: `num_ctx` is not sent there, the form disables the field and says why, and
`--help` names it as Ollama's. `reasoning` keeps its tri-state on both -- Ollama's
own `think` option, and vLLM's `chat_template_kwargs.enable_thinking`, which is
the switch the templates of the thinking models it serves read
([ADR-0019](0019-default-to-a-thinking-model.md)).

`OllamaUnavailableError` is renamed `ModelUnavailableError`. The failure it names
is unchanged and it is still one of exactly three (ADR-0009) mapped onto 503; what
was wrong was the name, which would have been raised for a vLLM that Ollama had
nothing to do with. The sentence it carries is the provider's to write, and that
sentence is the whole value of the exception.

`$VLLM_API_KEY` is the only way to set a key for a vLLM started with `--api-key`.
There is no flag, because a secret typed on a command line lands in a shell
history, and no field in `defaults_payload`, because that payload is handed to
every page that asks for the form. Blank -- the usual case -- sends a placeholder,
which is what a vLLM checking no key expects and what the OpenAI client insists
on having.

## Consequences

Someone with a GPU box already serving a model points this at it with
`--provider vllm` and runs on a model an order of magnitude larger than the one
their laptop fits, at a speed a batching server gives them. That is what this
buys, and it is the whole reason for it.

The cost is a second client library in `requirements.txt`. `langchain-openai`
(and `openai` under it) is installed whether or not vLLM is ever used, because
`providers.py` imports both at module level. Making one of them optional would
mean an import guard, a second failure mode for "the extra was not installed",
and a registry whose contents depend on what happens to be present -- a worse
trade than a dependency nobody notices.

What it obliges:

- **A provider is two halves in two modules, and both have to be written.**
  `config.PROVIDER_DEFAULTS` reads the environment; `providers.PROVIDERS` acts on
  a config. Neither can import the other's half, so the name is written twice and
  `tests/test_conventions.py` checks that the two tables agree -- behaviour with
  no defaults cannot be configured, and defaults with no behaviour are a
  `KeyError` the first time somebody chooses it.
- **A provider is offered in four places.** `--provider`'s choices,
  `api.PROVIDER_OPTIONS`, the rows `defaults_payload` sends the form, and the
  `ProviderOption` interface in `agent.types.ts`. The conventions tests read all
  four off the registry, which is the only reason a third provider is not five
  edits and a silent omission.
- **A setting one server takes and the other does not gets a declaration, not a
  branch.** `takes_num_ctx` is that declaration. The next such setting belongs
  beside it rather than as an `if provider == ...` in the CLI, the API and the
  form -- which is three places to forget.
- **The failure message stays the point.** Every hint names an address and a
  command that would fix it. A provider added without one is an exception that
  says a server is unavailable and leaves the shopper to guess which.
- **The nightly live run stays Ollama's.** [ADR-0026](0026-integration-tests-against-a-tiny-cpu-model.md)
  chose a model small enough to run on a CPU runner in five minutes; vLLM needs a
  GPU, so there is no honest way to put it in front of the same job. What
  `integration/` checks -- that schema-constrained decoding still holds against a
  real server -- is therefore checked for one of the two providers and asserted
  for the other only in `tests/test_providers.py`, which is a real gap and is
  named here rather than papered over.

ADR-0003 is not superseded. Local, no accounts, no keys to anybody's service:
that decision stands, and a vLLM on your own machine or your own network is
inside it rather than an exception to it.
