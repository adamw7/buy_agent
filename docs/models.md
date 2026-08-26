# Keeping the models current

The agent runs whatever model tag it is pointed at, and Ollama keeps that tag
current only when it is told to. This is the script that tells it, and how to
read what it says. Setting the model up in the first place is in the
[README](../README.md).

A model tag follows the registry, so re-pulling it is how a model is updated --
but `ollama pull` prints `success` whether it replaced anything or not.
`scripts/update_ollama.py` pulls the models Ollama has and compares the digests
either side of each pull, so the report says which builds actually moved. Run it
from the repository root:

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

Naming a tag Ollama does not have installs it; a pull the registry refuses is
reported against that model, the rest still run, and the script exits 1. The
Ollama server itself is a platform install (winget, the install script, the
macOS app) and is left to its own updater -- only the models are touched.
