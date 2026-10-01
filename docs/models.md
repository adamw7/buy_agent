# Keeping the models current

An Ollama tag follows the registry, so re-pulling it is how a model is updated,
but `ollama pull` prints `success` either way. `scripts/update_ollama.py` pulls
the installed models and compares digests either side, so it says which builds
moved. A vLLM is updated by restarting it, and a LiteLLM proxy wherever its
models are served, so this is Ollama's alone
([ADR-0028](adr/0028-serve-the-model-from-ollama-or-vllm.md),
[ADR-0068](adr/0068-reach-a-litellm-proxy-as-a-third-model-server.md)).

```powershell
python -m scripts.update_ollama                      # every installed model
python -m scripts.update_ollama llama3.2 qwen2.5:7b  # or only these
python -m scripts.update_ollama --base-url http://10.0.0.5:11434
```

```
llama3.2:latest  updated (a80c4f17acd5 -> 3f2a1b9c1d2e)
qwen2.5:7b       already current (845dbda0ea48)

2 model(s): 1 updated, 1 already current.
```

Naming a tag Ollama lacks installs it. A refused pull is reported against its
model, the rest still run, and the script exits 1. Ollama itself is left to its
own updater.
