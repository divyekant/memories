// Minimal, self-contained (no external assets) HTML pages for the OAuth login step.
// The single form field carrying real user input is the password; every oauth
// query param the browser arrived with is threaded back through as hidden
// inputs so the POST to /authorize sees the same {client_id, redirect_uri,
// state, code_challenge, code_challenge_method, scope} the GET saw.

const HIDDEN_FIELDS = ['client_id', 'redirect_uri', 'state', 'code_challenge', 'code_challenge_method', 'scope'];

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[c]));
}

function hiddenInputs(params = {}) {
  return HIDDEN_FIELDS.filter((k) => params[k] !== undefined && params[k] !== null)
    .map((k) => `<input type="hidden" name="${k}" value="${esc(params[k])}">`)
    .join('\n');
}

const STYLE = `body{font-family:Inter,system-ui,sans-serif;max-width:360px;margin:80px auto;padding:0 16px;color:#1a1a1a}
h1{font-size:20px}
p{color:#555;font-size:14px}
input[type=password]{width:100%;padding:8px;margin:8px 0;box-sizing:border-box;border:1px solid #ccc;border-radius:4px}
button{width:100%;padding:8px;background:#b8960f;color:#fff;border:0;border-radius:4px;font-size:14px;cursor:pointer}
.error{color:#DC2626;font-size:13px;margin:4px 0 0}`;

export function renderLogin({ clientName, error, params = {} } = {}) {
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Sign in</title>
<style>${STYLE}</style>
</head>
<body>
<h1>Sign in</h1>
<p>${esc(clientName || 'This app')} wants to access your memories.</p>
${error ? `<p class="error">${esc(error)}</p>` : ''}
<form method="POST" action="/authorize">
${hiddenInputs(params)}
<input type="password" name="password" placeholder="Password" autofocus required>
<button type="submit">Sign in</button>
</form>
</body>
</html>`;
}

