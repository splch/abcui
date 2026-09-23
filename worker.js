import {loadPyodide} from 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs';

const serve = loadPyodide().then(async pyodide => {
  pyodide.FS.mkdirTree('/home/pyodide/.nicegui');
  pyodide.FS.mount(pyodide.FS.filesystems.IDBFS, {}, '/home/pyodide/.nicegui');
  await new Promise(resolve => pyodide.FS.syncfs(true, resolve));
  setInterval(() => pyodide.FS.syncfs(false, () => {}), 1000);
  await pyodide.loadPackage('micropip');
  return pyodide.runPythonAsync(`
import asyncio, io, js, micropip, os, runpy, sys, sysconfig, tomllib, types, uuid, zipfile
from concurrent.futures import Future, ThreadPoolExecutor
from importlib.metadata import requires
from pyodide.ffi import to_js
from pyodide.http import pyfetch


class SemLock:
    SEM_VALUE_MAX = 2 ** 31 - 1

    def __init__(self, *args):
        raise NotImplementedError('a browser has no processes')


def submit(self, function, /, *args, **kwargs):
    future = Future()
    try:
        future.set_result(function(*args, **kwargs))
    except Exception as error:
        future.set_exception(error)
    return future


sys.modules.update(fastuuid=uuid, tokenizers=types.SimpleNamespace(Tokenizer=object),
                   _multiprocessing=types.SimpleNamespace(SemLock=SemLock, sem_unlink=None),
                   _posixshmem=types.SimpleNamespace(shm_unlink=None))
lock = {package['name']: package for package in tomllib.loads(await (await pyfetch('uv.lock')).string())['package']}
names = [dependency['name'] for dependency in lock['abcui']['dependencies']]
for name in names:
    wheel = max(lock[name]['wheels'], key=lambda wheel: 'manylinux' in wheel['url'])['url']
    zipfile.ZipFile(io.BytesIO(await (await pyfetch(wheel)).bytes())).extractall(sysconfig.get_path('purelib'))
skip = 'aiohttp', 'uvicorn', 'watchfiles', 'fastuuid', 'tokenizers', 'boto3'
await micropip.install(['aiohttp', 'uvicorn', 'pyyaml', 'packaging', *(
    requirement.split(';')[0] for name in names for requirement in requires(name) or []
    if 'extra ==' not in requirement and not requirement.startswith(skip))])

ThreadPoolExecutor.submit = submit
import anyio.to_thread
anyio.to_thread.run_sync = lambda call, *args, **_: asyncio.get_running_loop().run_in_executor(None, call, *args)
os.environ.update(NICEGUI_USER_SIMULATION='1', DISABLE_AIOHTTP_TRANSPORT='True', LITELLM_LOCAL_MODEL_COST_MAP='True')
from litellm.llms.gemini.audio_transcription.transformation import GeminiAudioTranscriptionConfig as Transcription
validate = Transcription.validate_environment
Transcription.validate_environment = lambda *args, **kwargs: {
    name: value for name, value in validate(*args, **kwargs).items() if name != 'Api-Revision'}
from nicegui import app, core
app.config.socket_io_js_transports = ['polling']
with open('app.py', 'w') as source:
    source.write(await (await pyfetch('app.py', cache='no-cache')).string())
runpy.run_path('app.py', run_name='__main__')
await core.app.router.lifespan_context(core.app).__aenter__()
root = js.URL.new('app', js.location).pathname


async def serve(request):
    url, data, done = js.URL.new(request.url), request.body.to_bytes(), asyncio.get_running_loop().create_future()
    body, response = [{'type': 'http.request', 'body': data}], {'status': 500, 'headers': [], 'body': b''}

    async def receive():
        return body.pop() if body else await done

    async def send(message):
        if message['type'] == 'http.response.start':
            response.update(status=message['status'], headers=[[k.decode(), v.decode()] for k, v in message['headers']])
        response['body'] += message.get('body', b'')

    headers = [[b'host', url.host.encode()], [b'content-length', str(len(data)).encode()],
               *([name.encode(), value.encode()] for name, value in request.headers)]
    await core.app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': request.method,
                    'scheme': url.protocol[:-1], 'path': url.pathname, 'query_string': url.search[1:].encode(),
                    'root_path': root, 'headers': headers, 'client': None, 'server': None}, receive, send)
    done.set_result({'type': 'http.disconnect'})
    return to_js(response)

serve
`);
});

onconnect = ({ports: [port]}) => port.onmessage = ({data, ports: [reply]}) => serve.then(handle => handle(data))
  .catch(error => ({status: 500, headers: [['content-type', 'text/plain']], body: new TextEncoder().encode(error)}))
  .then(response => reply.postMessage(response));
