const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { Client, LocalAuth } = require('whatsapp-web.js');
const QRCode = require('qrcode');

const root = path.resolve(__dirname, '../..');
const runDir = path.join(root, '.run');
const tokenPath = path.join(runDir, 'bridge-token');
const envPath = path.join(root, 'backend', '.env');
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, 'utf8').split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$/);
    if (match && !Object.hasOwn(process.env, match[1])) process.env[match[1]] = match[2].replace(/^['"]|['"]$/g, '');
  }
}
const bridgeToken = fs.existsSync(tokenPath) ? fs.readFileSync(tokenPath, 'utf8').trim() : process.env.WHATSAPP_BRIDGE_TOKEN;
if (!bridgeToken) { console.error('Missing local bridge token. Run start.ps1.'); process.exit(1); }
const profileDir = process.env.WHATSAPP_SESSION_DIR || path.join(process.env.LOCALAPPDATA || root, 'PersonalAIWhatsAppAssistant', 'session');
let client = null;
let status = 'DISCONNECTED';
let detail = 'WhatsApp Web is disconnected.';
let qr = null;
let qrGeneration = 0;
let retryTimer = null;
let explicitlyDisconnected = false;
let initPending = false;
let clearSessionOnRetry = false;
let socketRecoveryUsed = false;

function cleanError(error) {
  const message = String(error?.message || error || 'Unknown WhatsApp Web error');
  return message.replace(/[\r\n]+/g, ' ').slice(0, 220);
}
function setState(next, message) { status = next; detail = message; }
function scheduleReconnect(instance, delay = 5000) {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(async () => {
    if (explicitlyDisconnected) return;
    if (client === instance) client = null;
    try { await instance.destroy(); } catch {}
    if (clearSessionOnRetry) {
      clearSessionOnRetry = false;
      try { await fs.promises.rm(profileDir, { recursive: true, force: true }); }
      catch (error) { console.error(`Could not clear the expired WhatsApp Web session: ${cleanError(error)}`); }
    }
    await initialize();
  }, delay);
}
function tokenOK(req) { return req.headers.authorization === `Bearer ${bridgeToken}`; }
function json(res, code, value) {
  const body = Buffer.from(JSON.stringify(value));
  res.writeHead(code, { 'Content-Type': 'application/json', 'Content-Length': body.length, 'Cache-Control': 'no-store' });
  res.end(body);
}
function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => { body += chunk; if (body.length > 16000) reject(new Error('Request too large')); });
    req.on('end', () => { try { resolve(JSON.parse(body || '{}')); } catch { reject(new Error('Invalid JSON')); } });
    req.on('error', reject);
  });
}
function postIncoming(data) {
  return new Promise((resolve, reject) => {
    const body = Buffer.from(JSON.stringify(data));
    const req = http.request({ hostname: '127.0.0.1', port: 8000, path: '/api/whatsapp/incoming/', method: 'POST', headers: {
      'Content-Type': 'application/json', 'Content-Length': body.length, 'X-Bridge-Token': bridgeToken,
    } }, res => { res.resume(); res.on('end', () => res.statusCode >= 200 && res.statusCode < 300 ? resolve() : reject(new Error(`Local API returned HTTP ${res.statusCode}`))); });
    req.setTimeout(6000, () => req.destroy(new Error('Local API timed out')));
    req.on('error', reject); req.end(body);
  });
}
async function initialize() {
  if (initPending || (client && ['CONNECTING', 'WAITING_FOR_QR', 'CONNECTED'].includes(status))) return;
  initPending = true;
  explicitlyDisconnected = false;
  qr = null;
  setState('CONNECTING', 'Starting the personal WhatsApp Web session.');
  console.log('Launching the WhatsApp Web browser client.');
  const instance = new Client({
    authStrategy: new LocalAuth({ clientId: 'personal-assistant', dataPath: profileDir }),
    // WhatsApp Web can take several minutes to initialize large/older accounts.
    // Puppeteer's default 180s CDP timeout was aborting during page evaluation.
    puppeteer: { headless: true, protocolTimeout: 600000, timeout: 120000 },
    webVersionCache: { type: 'none' },
    takeoverOnConflict: false,
  });
  client = instance;
  instance.on('qr', async value => {
    try {
      const generation = ++qrGeneration;
      const dataUrl = await QRCode.toDataURL(value, { errorCorrectionLevel: 'M', margin: 2, width: 320 });
      if (instance !== client || generation !== qrGeneration) return;
      qr = dataUrl;
      setState('WAITING_FOR_QR', 'Open WhatsApp on your phone, choose Linked devices, then scan this QR code.');
      console.log('WhatsApp Web QR code is ready.');
    } catch (error) { qr = null; setState('CONNECTION_FAILED', `Could not render the login QR code: ${cleanError(error)}`); }
  });
  instance.on('authenticated', () => { qr = null; setState('CONNECTING', 'WhatsApp accepted the QR login; opening the linked session.'); console.log('WhatsApp Web authenticated.'); });
  instance.on('ready', () => { qr = null; socketRecoveryUsed = false; setState('CONNECTED', 'Personal WhatsApp Web session is ready.'); console.log('WhatsApp Web client is ready.'); });
  instance.on('auth_failure', message => {
    qr = null;
    clearSessionOnRetry = true;
    setState('CONNECTION_FAILED', `WhatsApp session authentication failed: ${cleanError(message)}. A new QR attempt will start shortly.`);
    scheduleReconnect(instance, 5000);
  });
  instance.on('message', async message => {
    if (message.fromMe || !/@(?:c\.us|lid)$/.test(message.from || '') || message.from === 'status@broadcast') return;
    try {
      let contact = {};
      try { contact = await message.getContact(); } catch {}
      const content = (message.body || '').trim();
      const type = message.type === 'chat' ? 'TEXT' : String(message.type || 'other').toUpperCase().slice(0, 24);
      await postIncoming({
        identifier: message.from, contact_name: contact.pushname || contact.name || message.from,
        external_id: message.id?._serialized, content: content || `[${type.toLowerCase()} message received]`,
        type, from_me: false,
      });
    } catch (error) { console.error(`Incoming message handoff failed: ${cleanError(error)}`); }
  });
  instance.on('disconnected', reason => {
    if (instance !== client) return;
    if (/logout|logged out/i.test(String(reason || ''))) clearSessionOnRetry = true;
    client = null; qr = null; initPending = false;
    setState(explicitlyDisconnected ? 'DISCONNECTED' : 'CONNECTION_FAILED', explicitlyDisconnected ? 'Disconnected by the user.' : `WhatsApp Web disconnected: ${cleanError(reason)}`);
    console.error(`WhatsApp Web disconnected: ${cleanError(reason)}`);
    if (!explicitlyDisconnected) scheduleReconnect(instance);
  });
  try {
    await instance.initialize();
    console.log('WhatsApp Web initialization completed.');
  } catch (error) {
    if (instance === client) {
      clearTimeout(retryTimer);
      console.error(error?.stack || `WhatsApp Web initialization failed: ${cleanError(error)}`);
      if (/Cannot read properties of null \(reading 'Socket'\)/i.test(String(error?.message || error)) && !socketRecoveryUsed) {
        socketRecoveryUsed = true;
        clearSessionOnRetry = true;
        qr = null;
        setState('CONNECTING', 'WhatsApp Web returned an invalid login page. Resetting the stale local session and retrying once for a fresh QR.');
        try {
          const diagnostics = await instance.pupPage?.evaluate(() => ({
            page: location.pathname,
            root: document.documentElement?.id || null,
            version: window.Debug?.VERSION || null,
            socketModuleAvailable: Boolean(window.require?.('WAWebSocketModel')?.Socket),
          }));
          console.error('WhatsApp Web page diagnostics: ' + JSON.stringify(diagnostics));
        } catch {}
        scheduleReconnect(instance, 2000);
        return;
      }
      client = null; qr = null;
      setState('CONNECTION_FAILED', `Could not start WhatsApp Web: ${cleanError(error)}. Check connectivity and select Connect WhatsApp to retry.`);
      try { await instance.destroy(); } catch {}
    }
  } finally { initPending = false; }
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') return json(res, 200, { ok: true });
  if (!tokenOK(req)) return json(res, 401, { error: 'Unauthorized local bridge request.' });
  try {
    if (req.method === 'GET' && req.url === '/status') return json(res, 200, { status, detail, qr, connected: status === 'CONNECTED' });
    if (req.method === 'POST' && req.url === '/connect') {
      await initialize();
      return json(res, 200, { status, detail, qr, connected: status === 'CONNECTED' });
    }
    if (req.method === 'POST' && req.url === '/disconnect') {
      explicitlyDisconnected = true; clearTimeout(retryTimer); qr = null;
      const active = client; client = null; initPending = false;
      if (active) { try { await active.logout(); } catch {} try { await active.destroy(); } catch {} }
      await fs.promises.rm(profileDir, { recursive: true, force: true });
      setState('DISCONNECTED', 'Logged out and removed the saved WhatsApp session.');
      return json(res, 200, { status, detail });
    }
    if (req.method === 'POST' && req.url === '/send') {
      if (status !== 'CONNECTED' || !client) return json(res, 409, { error: 'Personal WhatsApp is not connected.' });
      const data = await readBody(req);
      if (typeof data.recipient !== 'string' || !/@(?:c\.us|lid)$/.test(data.recipient) || typeof data.text !== 'string' || !data.text.trim() || data.text.length > 4096) return json(res, 400, { error: 'A direct contact and text message are required.' });
      const sent = await client.sendMessage(data.recipient, data.text.trim());
      return json(res, 200, { id: sent.id?._serialized || null });
    }
    return json(res, 404, { error: 'Not found.' });
  } catch (error) { return json(res, 500, { error: cleanError(error) }); }
});
server.listen(8765, '127.0.0.1', () => { console.log('WhatsApp Web sidecar listening on 127.0.0.1:8765; waiting for Connect WhatsApp.'); });
process.on('unhandledRejection', reason => {
  console.error(reason?.stack || `Unhandled sidecar rejection: ${cleanError(reason)}`);
  if (/Cannot read properties of null \(reading 'Socket'\)/i.test(String(reason?.message || reason)) && client && !socketRecoveryUsed) {
    socketRecoveryUsed = true;
    clearSessionOnRetry = true;
    qr = null;
    setState('CONNECTION_FAILED', 'WhatsApp Web page became invalid. Resetting the local session and requesting a fresh QR.');
    scheduleReconnect(client, 2000);
  }
});
process.on('SIGINT', async () => { clearTimeout(retryTimer); try { await client?.destroy(); } catch {} server.close(() => process.exit(0)); });
process.on('SIGTERM', async () => { clearTimeout(retryTimer); try { await client?.destroy(); } catch {} server.close(() => process.exit(0)); });
