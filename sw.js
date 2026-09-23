addEventListener('install', () => skipWaiting());
addEventListener('fetch', event => {
  const app = registration.scope + 'app/';
  if (event.request.url.startsWith(app)) event.respondWith((async () => {
    const shell = (await clients.matchAll({includeUncontrolled: true})).find(page => !page.url.startsWith(app));
    if (!shell) return Response.redirect(registration.scope);
    const {port1, port2} = new MessageChannel(), {method, url, headers} = event.request;
    const body = new Uint8Array(await event.request.arrayBuffer());
    shell.postMessage({method, url, headers: [...headers], body}, [port2]);
    const response = await new Promise(resolve => port1.onmessage = message => resolve(message.data));
    return new Response(response.body.length ? response.body : null, response);
  })());
});
