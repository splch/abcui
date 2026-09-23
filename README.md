# ABC UI

A chat UI like [Open WebUI](https://openwebui.com) in one file: `app.py` is the whole app, under 250 lines of [NiceGUI](https://nicegui.io) and [LiteLLM](https://docs.litellm.ai). It runs entirely in your browser at https://abcui.xyz, with no server behind it.

It streams replies and reasoning from any LiteLLM model and gives the model four tools: web search, a Python runner, image generation, and search over the PDFs and text files you upload. It sees the images you attach, takes voice messages, and reads its replies aloud. It saves your chats and generated images in the browser, and you can stop, regenerate, edit, or copy any turn.

## Run

Open https://abcui.xyz. The first visit downloads Python and the packages, about 60 MB, which the browser then caches.

[Pyodide](https://pyodide.org) runs `app.py` in a shared worker that every tab uses, a service worker hands it the page's requests in place of a web server, and `index.html` shows the app. To serve your own copy, publish this folder with any static host, such as GitHub Pages or `python -m http.server`. With [uv](https://docs.astral.sh/uv/) installed, `uv run app.py` still runs the app as a local server.

## Configure

The defaults use Gemini for chat, embeddings, images, speech, and transcription, and Tavily for search. Paste a Gemini API key and a Tavily API key into the settings behind the gear icon; each edit saves in the browser at once. `MODELS` (comma-separated), `EMBED`, `IMAGE`, `STT`, and `TTS` take any LiteLLM model id whose provider accepts requests from a browser; `VOICE` names the speaking voice, `SEARCH` names the search provider, and `SYSTEM` is the system prompt, whose `strftime` codes become today's date. The defaults sit at the top of `app.py`, and the browser installs the NiceGUI, LiteLLM, and pypdf versions that `uv.lock` pins.

## Security

The Python tool runs model-written code in the browser, which keeps it away from your files and programs. The code can still read your keys and chats and send them anywhere, and a web page or file the model reads could talk it into doing so, so use keys you can revoke. `uv run app.py` runs that code inside the local server, so the server listens only on localhost.
