# ABC UI

A chat UI like [Open WebUI](https://openwebui.com) in one file: `app.py` is the whole app, under 200 lines of [NiceGUI](https://nicegui.io) and [LiteLLM](https://docs.litellm.ai).

It streams replies from any LiteLLM model and gives the model four tools: web search, a Python runner, image generation, and search over the PDFs and text files you upload. It sees the images you attach, takes voice messages, and reads its replies aloud.

## Run

With [uv](https://docs.astral.sh/uv/) installed:

```sh
uv run app.py
```

The app opens at http://127.0.0.1:8080.

## Configure

The defaults expect four local servers and need no API keys: Ollama for chat and embeddings, Speaches for speech, stable-diffusion.cpp for images, and SearXNG for search. To change them, set environment variables: `MODELS` (comma-separated), `EMBED`, `IMAGE`, `STT`, and `TTS` take any LiteLLM model id; `VOICE` names the speaking voice, and `SEARCH` names the search provider. The defaults and the servers' addresses sit at the top of `app.py`.

```sh
OPENAI_API_KEY=... MODELS=openai/gpt-5,ollama/llama3.2 uv run app.py
```

## Security

The Python tool runs model-written code with no sandbox, so the server listens only on localhost. If you tunnel or proxy it, anyone who reaches the URL can run code on your machine.
