const API_PATHS = new Set(['/api/status','/api/tools','/api/events','/api/runs','/api/calls','/api/resources','/api/diagnostics']);
const BACKEND = 'https://ncp-control.notation-systems-lab.workers.dev';
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    // Hosted dispatch enforces the preserved owner-private Site policy. No
    // caller-supplied destination, auth header or backend path is forwarded.
    if (API_PATHS.has(url.pathname)) {
      if (!env.NCP_BACKEND_TOKEN) return Response.json({error:{code:'backend_not_configured'}},{status:503});
      if (request.headers.has('origin') && request.headers.get('origin') !== url.origin)
        return Response.json({error:{code:'origin_denied'}},{status:403});
      if (!['GET','POST'].includes(request.method)) return new Response('Method not allowed',{status:405});
      const headers = {'Authorization': 'Bearer '+env.NCP_BACKEND_TOKEN};
      if (request.headers.has('content-type')) headers['Content-Type'] = request.headers.get('content-type');
      try {
        const response = await fetch(BACKEND+url.pathname, {method:request.method, headers,
          body: request.method==='POST' ? request.body : undefined, redirect:'manual'});
        if (response.status >= 300 && response.status < 400) throw new Error('Backend redirect refused');
        return new Response(response.body,{status:response.status,headers:{'Content-Type':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
      } catch (error) {
        console.error('Backend transport failed:', String(error).replaceAll(env.NCP_BACKEND_TOKEN, '[redacted]').slice(0, 300));
        return Response.json({error:{code:'backend_unreachable',message:'Do not automatically repeat a state-changing request.'}},{status:502});
      }
    }
    if (request.method !== 'GET' || !Object.hasOwn(ASSETS,url.pathname)) return new Response('Not found',{status:404});
    const mime = url.pathname==='/LICENSE' ? 'text/plain' : url.pathname.endsWith('.js') ? 'text/javascript' : url.pathname.endsWith('.css') ? 'text/css' : url.pathname.endsWith('.json') ? 'application/json' : 'text/html';
    return new Response(ASSETS[url.pathname],{headers:{'Content-Type':mime+'; charset=utf-8','Cache-Control':'no-store',
      'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
      ...(url.pathname==='/' ? {'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'"} : {})}});
  }
};
