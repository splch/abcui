import asyncio, json, os, tempfile
from pathlib import Path

APP = Path(__file__).resolve().with_name('app.py')
assert len(APP.read_bytes().splitlines()) < 250, 'the README promises an app.py under 250 lines'
# NiceGUI keeps chats under the working directory and a simulation deletes them, so leave before nicegui is imported.
os.chdir(tempfile.mkdtemp())
os.environ['PYTEST_CURRENT_TEST'] = ''  # outside pytest, user_simulation puts NiceGUI in script mode and no page opens

import litellm
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices
from nicegui import app, ui
from nicegui.testing import user_simulation

# The scripted model reasons, calls the real run_python, then repeats what the tool returned.
CALL = {'index': 0, 'id': '1', 'type': 'function',
        'function': {'name': 'run_python', 'arguments': json.dumps({'code': "print('✓', 2 ** 100)"})}}
REPLY = f'It is ✓ {2 ** 100}'


async def stream(deltas):
    for delta in deltas:
        yield ModelResponseStream(choices=[StreamingChoices(delta=Delta(**delta))])


async def model(messages, **_):
    last = messages[-1]
    return stream([{'content': f"It is {last['content']}"}] if last['role'] == 'tool' else
                  [{'reasoning_content': 'Let me compute.'}, {'tool_calls': [CALL]}])


async def main():
    litellm.acompletion = model
    async with user_simulation(main_file=APP) as user:
        await user.open('/')
        user.find(ui.textarea).type('What is 2 ** 100?')
        user.find(kind=ui.button, content='send').click()
        await user.should_see(REPLY, retries=600)
        await user.should_see('Let me compute.')
        user.find(kind=ui.button, content='refresh').click()
        await user.should_not_see(REPLY)
        await user.should_see(REPLY, retries=600)
        chat, = app.storage.general['chats'].values()
        assert [message['role'] for message in chat] == ['user', 'assistant', 'tool', 'assistant'], chat
        user.find(kind=ui.button, content='settings').click()
        user.find(kind=ui.input, content='SYSTEM').type(' Be brief.')
        assert app.storage.general['settings']['SYSTEM'] == os.environ['SYSTEM'] != 'Today is %A, %d %B %Y.'
        await user.open('/')
        user.find(ui.item).click()
        await user.should_see(REPLY)
        await user.should_see('Let me compute.')
        await user.should_see('Be brief.')  # the saved setting fills a new page's dialog


asyncio.run(main())
