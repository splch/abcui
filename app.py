import asyncio, base64, inspect, io, json, math, os, subprocess, time

import litellm, pypdf
from nicegui import app, ui

# Each model is any LiteLLM id; override them from the environment, e.g. MODELS=openai/gpt-5,ollama/llama3.2
MODELS = os.getenv('MODELS', 'ollama_chat/alfred,ollama_chat/qwen3.8-abliterated:27b-nothink,'
                   'ollama_chat/gemma4:e4b').split(',')
EMBED = os.getenv('EMBED', 'ollama/embeddinggemma:300m')
IMAGE = os.getenv('IMAGE', 'lm_studio/sd-cpp-local')
STT = os.getenv('STT', 'hosted_vllm/Systran/faster-distil-whisper-large-v3')
TTS = os.getenv('TTS', 'hosted_vllm/speaches-ai/Kokoro-82M-v1.0-ONNX')
VOICE = os.getenv('VOICE', 'af_heart')
SEARCH = os.getenv('SEARCH', 'searxng')
SYSTEM = os.getenv('SYSTEM', 'Today is %A, %d %B %Y.')
# The ids above reach this server's OpenAI-compatible hosts through LiteLLM's hosted_vllm and lm_studio providers.
os.environ.setdefault('HOSTED_VLLM_API_BASE', 'http://172.19.0.44:8000/v1')  # Speaches
os.environ.setdefault('LM_STUDIO_API_BASE', 'http://192.168.1.2:7860/v1')  # stable-diffusion.cpp
os.environ.setdefault('SEARXNG_API_BASE', 'http://127.0.0.1:8899')
os.environ.setdefault('OPENAI_API_KEY', 'local')  # LiteLLM's OpenAI client demands a key; Speaches ignores it

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
ASK = '() => confirm("Delete this message and all after it?") && emit()'
chats = app.storage.general.setdefault('chats', {})
docs, running = app.storage.general.setdefault(f'docs {EMBED}', {}), {}


async def embed(texts):
    # batches of 100, the most that Gemini accepts per request
    batches = [await litellm.aembedding(model=EMBED, input=texts[i:i + 100]) for i in range(0, len(texts), 100)]
    return [item['embedding'] for batch in batches for item in batch.data]


async def web_search(query: str):
    """Search the web for current information."""
    results = (await litellm.asearch(query=query, search_provider=SEARCH, max_results=5)).results[:5]
    return '\n\n'.join(f'{result.title}\n{result.url}\n{result.snippet}' for result in results)


async def run_python(code: str):
    """Run a Python script and return its output. Declare dependencies as PEP 723 inline script metadata."""
    # uv reads the script from stdin and installs whatever it declares into a cached, throwaway environment
    done = await asyncio.to_thread(subprocess.run, 'setpriv --no-new-privs timeout -v 120 uv run --quiet --no-project -'
                                   .split(), input=code, capture_output=True, text=True, cwd='/tmp')
    return (done.stdout + done.stderr)[-20000:]


async def generate_image(prompt: str):
    """Generate an image from a text prompt and show it to the user."""
    image = (await litellm.aimage_generation(model=IMAGE, prompt=prompt)).data[0]
    app.storage.client['image'] = image.url or (path := f'.nicegui/{time.time()}.png')
    image.url or open(path, 'wb').write(base64.b64decode(image.b64_json))
    return 'The image was shown to the user.'


async def search_files(query: str):
    """Search the user's uploaded files for relevant passages."""
    q, files = (await embed([query]))[0], sum(docs.values(), [])
    score = lambda doc: sum(a * b for a, b in zip(q, doc[1])) / (math.hypot(*q) * math.hypot(*doc[1]))  # cosine
    return '\n---\n'.join(chunk for chunk, _ in sorted(files, key=score, reverse=True)[:5]) or 'No files found.'


# The model sees each tool as its name, its docstring and its (string) arguments.
TOOLS = {tool.__name__: tool for tool in (web_search, run_python, generate_image, search_files)}
SPECS = [{'type': 'function', 'function': {'name': name, 'description': tool.__doc__, 'parameters': {
    'type': 'object', 'properties': {arg: {'type': 'string'} for arg in inspect.signature(tool).parameters},
    'required': list(inspect.signature(tool).parameters)}}} for name, tool in TOOLS.items()]


async def call_tool(call):
    args = json.loads(call['function']['arguments'])
    try:
        result = str(await TOOLS[call['function']['name']](**args))
    except Exception as error:  # reported to the model, which can then correct itself or explain
        result = f'Error: {error}'
    return {'role': 'tool', 'tool_call_id': call['id'], 'content': result, 'name': call['function']['name'],
            'input': '\n'.join(map(str, args.values())), 'image': app.storage.client.pop('image', None)}


def draw(message):
    if message['role'] == 'tool':
        with ui.expansion(message['name'], icon='build').classes('w-full'):
            ui.code(message['input'])
            ui.code(message['content'].replace('```', "'''"), language='text')
        return message['image'] and ui.image(message['image']).classes('w-96')
    with ui.expansion('Thinking', icon='psychology').classes('w-full') as expansion:
        thinking = ui.markdown(message.get('reasoning_content', '')).bind_content_to(expansion, 'visible')
    return thinking, ui.markdown(message.get('content', ''))


async def respond(model, messages):
    tools = SPECS if await asyncio.to_thread(litellm.supports_function_calling, model) else None
    while messages[-1]['role'] != 'assistant':
        (thinking, markdown), chunks = draw({'role': 'assistant'}), []
        sent = [{k: v for k, v in m.items() if k not in {'model', 'name', 'input', 'image'}} for m in messages]
        SYSTEM and sent.insert(0, {'role': 'system', 'content': time.strftime(SYSTEM)})
        async for chunk in await litellm.acompletion(model=model, messages=sent, tools=tools, stream=True):
            chunks.append(chunk)
            markdown.content += chunk.choices[0].delta.content or ''
            thinking.content += getattr(chunk.choices[0].delta, 'reasoning_content', None) or ''
            ui.run_javascript('scrollY + 2 * innerHeight > document.body.scrollHeight && scrollTo(0, 1e9)')
        turn = [litellm.stream_chunk_builder(chunks).choices[0].message.model_dump(exclude_none=True)]
        for call in turn[0].get('tool_calls', []):
            turn.append(await call_tool(call)) or draw(turn[-1])
        messages.extend(turn)


def root():
    key, parts = str(time.time()), []
    ui.on_exception(lambda error: ui.notify(str(error)[:600], type='negative', multi_line=True))
    busy = lambda: key in running

    async def say(content):
        speech = await litellm.aspeech(model=TTS, voice=VOICE, input=content, response_format='mp3')
        ui.audio(f'data:audio/mpeg;base64,{base64.b64encode(speech.content).decode()}', autoplay=True)

    async def record():
        mic.props('color=red')
        await ui.run_javascript('try {%s} catch (error) {alert(error)}' % (RECORD % upload.id), timeout=3600)
        mic.props('color=primary')

    async def attach(event):
        data, kind, name = await event.file.read(), event.file.content_type, event.file.name
        if kind.startswith('audio/'):  # a recording is transcribed, sent, and answered aloud
            text.value = (await litellm.atranscription(model=STT, file=(name, data, kind))).text
            return await send(speak=True)
        if kind.startswith('image/'):
            url = f'data:{kind};base64,{base64.b64encode(data).decode()}'
            parts.append({'type': 'image_url', 'image_url': {'url': url}})
        else:
            content = ('\n'.join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(data)).pages)
                       if kind == 'application/pdf' else data.decode(errors='ignore'))
            chunks = [content[i:i + 1000] for i in range(0, len(content), 800)]
            docs.setdefault(key, []).extend(zip(chunks, await embed(chunks)))
            parts.append({'type': 'text', 'text': f'📎 {name} (uploaded: read it with search_files)'})
        ui.notify(f'📎 {name}')

    def show(index):
        with chat, ui.chat_message(sent=True).classes('whitespace-pre-wrap'):
            for part in chats[key][index]['content']:
                ui.image(part['image_url']['url']).classes('w-64') if 'image_url' in part else ui.label(part['text'])
        with chat, ui.chat_message(name=chats[key][index]['model']).props('bg-color=grey-2') as reply:
            bubble = ui.column().classes('w-full')
        answer = lambda: ([child.content for child in bubble if isinstance(child, ui.markdown)] or [''])[-1]
        with reply.add_slot('stamp'):
            ui.button(icon='volume_up', on_click=lambda: say(answer())).props('flat dense')
            ui.button(icon='content_copy', on_click=lambda: ui.clipboard.write(answer())).props('flat dense')
            ui.button(icon='refresh').props('flat dense') \
                .on('click', lambda: busy() or edit(index) or send(), js_handler=ASK)
            ui.button(icon='edit').props('flat dense').on('click', lambda: busy() or edit(index), js_handler=ASK)
        return bubble

    def load(new=None):
        nonlocal key
        key, parts[:] = new or str(time.time()), []
        upload.reset()
        chat.clear()
        history.refresh()
        for index, message in enumerate(chats.get(key, [])):
            if message['role'] == 'user':
                bubble = show(index)
            else:
                with bubble:
                    draw(message)

    def edit(index):
        content = chats[key][index]['content']
        del chats[key][index:]
        load(key)
        text.value, parts[:] = content[0]['text'], content[1:]

    async def send(speak=False):
        if not text.value.strip() or busy():
            return
        messages = chats.setdefault(key, [])
        messages.append(dict(role='user', model=model.value, content=[{'type': 'text', 'text': text.value}, *parts]))
        history.refresh()
        bubble = show(len(messages) - 1)
        with chat, ui.button(icon='stop', on_click=asyncio.current_task().cancel).props('flat') as running[key]:
            ui.spinner('dots', size='lg')
            ui.run_javascript('scrollTo(0, 1e9)')
        text.value, parts[:] = '', []
        upload.reset()
        with bubble:
            try:
                await respond(model.value, messages)
                speak and await say(messages[-1].get('content', ''))
            except Exception as error:
                app.handle_exception(error)
            finally:
                running.pop(key).delete()

    @ui.refreshable
    def history():
        for old in reversed([old for old in chats if chats[old] and chats[old][0]['role'] == 'user']):
            ui.item(chats[old][0]['content'][0]['text'][:40], on_click=lambda old=old: busy() or load(old))

    with ui.left_drawer().classes('bg-grey-2') as drawer, ui.list().classes('w-full'):
        history()
    with ui.header().classes('items-center'):
        ui.button(icon='menu', on_click=drawer.toggle).props('flat round color=white')
        model = ui.select(MODELS, value=MODELS[0], with_input=True, new_value_mode='add-unique') \
            .props('dense dark borderless').classes('w-80 mr-auto')
        ui.button(icon='delete', on_click=lambda: busy() or (chats.pop(key, 0), docs.pop(key, 0), load())) \
            .props('flat round color=white')
        ui.button(icon='add', on_click=lambda: busy() or load()).props('flat round color=white')
    chat = ui.column().classes('w-full max-w-3xl mx-auto items-stretch')
    upload = ui.upload(multiple=True, auto_upload=True, on_upload=attach).classes('hidden')
    with ui.footer().classes('bg-white'), ui.row().classes('w-full max-w-3xl mx-auto no-wrap items-center'):
        ui.button(icon='attach_file', on_click=lambda: upload.run_method('pickFiles')).props('flat round')
        text = ui.textarea(placeholder='Message').props('autogrow outlined dense rows=1').classes('grow') \
            .on('keydown.enter.exact.prevent', send)
        mic = ui.button(icon='mic', on_click=record).props('flat round')
        ui.button(icon='send', on_click=send).props('flat round')


# Bound to localhost because run_python executes model-written code on this machine.
ui.run(root, title='ABC UI', host='127.0.0.1', reconnect_timeout=3600)
