import base64, inspect, io, json, math, os, subprocess, warnings

import litellm, pypdf
from nicegui import run, ui

# Each model is any LiteLLM id; override them from the environment, e.g. MODELS=openai/gpt-5,ollama/llama3.2
MODELS = os.getenv('MODELS', 'gemini/gemini-3.1-flash-lite,gemini/gemini-3.8-flash').split(',')
EMBED = os.getenv('EMBED', 'gemini/gemini-embedding-001')
IMAGE = os.getenv('IMAGE', 'gemini/gemini-3.1-flash-image')
STT = os.getenv('STT', 'gemini/gemini-3.5-transcribe')
TTS = os.getenv('TTS', 'gemini/gemini-3.1-flash-tts-preview')
VOICE = os.getenv('VOICE', 'Kore')
SEARCH = os.getenv('SEARCH', 'tavily')
warnings.filterwarnings('ignore', 'Pydantic serializer warnings')  # LiteLLM re-serialises tool calls noisily

# Runs in the browser when the mic button is clicked: the first click starts a recording, the second one stops it.
# The audio is re-encoded as 16 kHz WAV (a 44-byte RIFF header plus 16-bit samples), which speech-to-text APIs accept;
# Gemini rejects the WebM and MP4 that browsers record. The hidden uploader (%d is its element id) then delivers the
# file to attach() like any other attachment, over HTTP rather than the size-limited websocket.
RECORD = '''
if (window.rec?.state === 'recording') return rec.stop();
const stream = await navigator.mediaDevices.getUserMedia({audio: true}), chunks = [];
window.rec = new MediaRecorder(stream);
rec.ondataavailable = event => chunks.push(event.data);
rec.start();
await new Promise(resolve => rec.onstop = resolve);
stream.getTracks().forEach(track => track.stop());
const audio = await new OfflineAudioContext(1, 1, 16000).decodeAudioData(await new Blob(chunks).arrayBuffer());
const pcm = Int16Array.from(audio.getChannelData(0), sample => sample * 32767), size = pcm.byteLength;
const header = [0x46464952, 36 + size, 0x45564157, 0x20746d66, 16, 0x10001, 16000, 32000, 0x100002, 0x61746164, size];
getElement(%d).$refs.qRef.addFiles([new File([new Uint32Array(header), pcm], 'voice.wav', {type: 'audio/wav'})]);
'''
docs = []  # (chunk, embedding) pairs of every uploaded document


async def embed(texts):
    # batches of 100, the most that Gemini accepts per request
    batches = [await litellm.aembedding(model=EMBED, input=texts[i:i + 100]) for i in range(0, len(texts), 100)]
    return [item['embedding'] for batch in batches for item in batch.data]


async def web_search(query: str):
    """Search the web for current information."""
    results = (await litellm.asearch(query=query, search_provider=SEARCH, max_results=5)).results
    return '\n\n'.join(f'{result.title}\n{result.url}\n{result.snippet}' for result in results)


async def run_python(code: str):
    """Run a Python script and return its output. Declare dependencies as PEP 723 inline script metadata."""
    # uv reads the script from stdin and installs whatever it declares into a cached, throwaway environment
    done = await run.io_bound(subprocess.run, ['uv', 'run', '--quiet', '--no-project', '-'], input=code,
                              capture_output=True, text=True, timeout=120)
    return done.stdout + done.stderr


async def generate_image(prompt: str):
    """Generate an image from a text prompt and show it to the user."""
    image = (await litellm.aimage_generation(model=IMAGE, prompt=prompt)).data[0]
    ui.image(image.url or f'data:image/png;base64,{image.b64_json}').classes('w-96')  # lands in the open chat bubble
    return 'The image was shown to the user.'


async def search_files(query: str):
    """Search the user's uploaded files for relevant passages."""
    q = (await embed([query]))[0]
    score = lambda doc: sum(a * b for a, b in zip(q, doc[1])) / (math.hypot(*q) * math.hypot(*doc[1]))  # cosine
    return '\n---\n'.join(chunk for chunk, _ in sorted(docs, key=score, reverse=True)[:5]) or 'No files found.'


# The model sees each tool as its name, its docstring and its (string) arguments.
TOOLS = {tool.__name__: tool for tool in (web_search, run_python, generate_image, search_files)}
SPECS = [{'type': 'function', 'function': {'name': name, 'description': tool.__doc__, 'parameters': {
    'type': 'object', 'properties': {arg: {'type': 'string'} for arg in inspect.signature(tool).parameters},
    'required': list(inspect.signature(tool).parameters)}}} for name, tool in TOOLS.items()]


async def call_tool(call):
    args = json.loads(call.function.arguments)
    try:
        result = str(await TOOLS[call.function.name](**args))
    except Exception as error:  # reported to the model, which can then correct itself or explain
        result = f'Error: {error}'
    with ui.expansion(call.function.name, icon='build').classes('w-full'):
        ui.code('\n'.join(map(str, args.values())))
        ui.code(result, language='text')
    return {'role': 'tool', 'tool_call_id': call.id, 'content': result}


async def respond(model, messages):
    tools = SPECS if litellm.supports_function_calling(model) else None
    while True:  # stream a reply into the open chat bubble, run the tools it calls, repeat until it calls none
        markdown, chunks = ui.markdown(), []
        async for chunk in await litellm.acompletion(model=model, messages=messages, tools=tools, stream=True):
            chunks.append(chunk)
            markdown.content += chunk.choices[0].delta.content or ''
            ui.run_javascript('window.scrollTo(0, document.body.scrollHeight)')
        reply = litellm.stream_chunk_builder(chunks).choices[0].message
        messages.append(reply)
        if not reply.tool_calls:
            return reply.content
        messages.extend([await call_tool(call) for call in reply.tool_calls])


def root():
    messages, parts = [], []  # the chat history and the attachments of the next message
    ui.on_exception(lambda error: ui.notify(str(error)[:600], type='negative', multi_line=True))

    async def say(content):
        speech = await litellm.aspeech(model=TTS, voice=VOICE, input=content)
        ui.audio(f'data:audio/wav;base64,{base64.b64encode(speech.content).decode()}', autoplay=True)

    async def record():
        mic.props('color=red')
        await ui.run_javascript(RECORD % upload.id, timeout=3600)
        mic.props('color=primary')

    async def attach(event):
        data, kind, name = await event.file.read(), event.file.content_type, event.file.name
        if kind.startswith('audio/'):  # a recording is transcribed, sent, and answered aloud
            text.value = (await litellm.atranscription(model=STT, file=(name, data, kind))).text
            return await send(speak=True)
        with chat, ui.chat_message(sent=True):
            if kind.startswith('image/'):
                url = f'data:{kind};base64,{base64.b64encode(data).decode()}'
                parts.append({'type': 'image_url', 'image_url': {'url': url}})
                ui.image(url).classes('w-64')
            else:  # documents are split into overlapping chunks and embedded for search_files
                content = ('\n'.join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(data)).pages)
                           if kind == 'application/pdf' else data.decode(errors='ignore'))
                chunks = [content[i:i + 1000] for i in range(0, len(content), 800)]
                docs.extend(zip(chunks, await embed(chunks)))
                parts.append({'type': 'text', 'text': f'[File "{name}" uploaded: read it with search_files]'})
                ui.label(f'📎 {name}')

    async def send(speak=False):
        if not text.value.strip():
            return
        messages.append({'role': 'user', 'content': [{'type': 'text', 'text': text.value}, *parts]})
        with chat:
            ui.chat_message(text.value, sent=True)
            with ui.chat_message(name=model.value).props('bg-color=grey-2'):
                bubble = ui.column().classes('w-full')  # Quasar would draw each direct child as a bubble of its own
            spinner = ui.spinner('dots', size='lg')
        text.value = ''
        parts.clear()
        upload.reset()
        try:
            with bubble:
                answer = await respond(model.value, messages)
                ui.button(icon='volume_up', on_click=lambda: say(answer)).props('flat dense')
                if speak:
                    await say(answer)
        finally:
            spinner.delete()

    with ui.header().classes('items-center justify-between'):
        model = ui.select(MODELS, value=MODELS[0], with_input=True, new_value_mode='add-unique') \
            .props('dense dark borderless').classes('w-80')
        ui.button(icon='add', on_click=lambda: (messages.clear(), parts.clear(), chat.clear())) \
            .props('flat round color=white')
    chat = ui.column().classes('w-full max-w-3xl mx-auto items-stretch')
    upload = ui.upload(multiple=True, auto_upload=True, on_upload=attach).classes('hidden')
    with ui.footer().classes('bg-white'), ui.row().classes('w-full max-w-3xl mx-auto no-wrap items-center'):
        ui.button(icon='attach_file', on_click=lambda: upload.run_method('pickFiles')).props('flat round')
        text = ui.textarea(placeholder='Message').props('autogrow outlined dense rows=1').classes('grow') \
            .on('keydown.enter.exact.prevent', send)
        mic = ui.button(icon='mic', on_click=record).props('flat round')
        ui.button(icon='send', on_click=send).props('flat round')


# Bound to localhost because run_python executes model-written code on this machine.
ui.run(root, title='abc-llm-ui', host='127.0.0.1')
