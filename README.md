# Personal AI WhatsApp Assistant

A local-first personal messaging assistant with a React dashboard, Django REST API, SQLite database, and background worker. It links to a normal personal WhatsApp account through a QR code using `whatsapp-web.js` and a local Chromium profile. Meta WhatsApp Business Platform credentials, a Business Portfolio, and a Business phone number are not used.

## Windows setup

Requirements: Python 3.11+, Node.js 20+, npm.

### One-time setup

In PowerShell, from this project folder:

```powershell
cd backend
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
cd ..\frontend
npm install
Copy-Item .env.example .env
cd ..\backend\whatsapp-web
npm install
cd ..\..
```

### Start, link, and stop

```powershell
.\start.ps1
```

Open http://127.0.0.1:5173 and choose **Connect WhatsApp** (or **Show / refresh QR code** in Settings). The sidecar stays idle until you click. On your phone open WhatsApp → **Linked devices** → **Link a device**, then scan the displayed QR. The sidecar restores the local session after app restarts; you do not need to scan every time unless WhatsApp expires the linked session. If you remove this linked device from your phone, the sidecar clears the invalid session and requests a fresh QR. Use **Disconnect and remove saved session** to log out and delete the profile. `stop.ps1` stops services but keeps the linked session.

### Remove the saved WhatsApp login

In the app, open **Settings** and choose **Disconnect and remove saved session**. This logs out this app's linked device and deletes its local login profile; connect again and scan a new QR code to link it.

If the app is stuck connecting and you cannot use Settings, stop the services and remove only this app's WhatsApp profile from PowerShell in the project folder:

```powershell
.\stop.ps1
$session = Join-Path $env:LOCALAPPDATA 'PersonalAIWhatsAppAssistant\session\session-personal-assistant'
if (Test-Path -LiteralPath $session) {
    icacls.exe $session /reset /T /C /Q
    if ($LASTEXITCODE -ne 0) { throw "Could not reset WhatsApp session permissions." }
    icacls.exe $session /grant:r "$($env:USERNAME):(OI)(CI)F" /T /C /Q
    if ($LASTEXITCODE -ne 0) { throw "Could not grant access to the WhatsApp session." }
    Remove-Item -LiteralPath $session -Recurse -Force
}
.\start.ps1
```

This removes the app's saved linked-device login, not your WhatsApp account or messages. After restarting, choose **Connect WhatsApp** and scan the new QR code from WhatsApp → **Linked devices** → **Link a device**. Running `stop.ps1` alone does not remove the saved login.

The scripts start Django on 127.0.0.1:8000, the assistant worker, Vite on 127.0.0.1:5173, and the authenticated WhatsApp Web sidecar on 127.0.0.1:8765. The sidecar is loopback-only and stays idle until you click **Connect WhatsApp**. Logs and process IDs are written to the ignored `.run/` folder. The bridge token is generated locally in `.run/bridge-token`. The session profile is stored under `%LOCALAPPDATA%\PersonalAIWhatsAppAssistant\session`; the launcher removes inherited ACLs and grants access to the current Windows user. Chromium protects some browser secrets with DPAPI, but the entire profile is not encrypted. Protect your Windows account and device, and do not copy or share the profile directory.

The integration is unofficial. `whatsapp-web.js` drives WhatsApp Web in its own headless Chromium browser, separate from your regular browser. The linked-device name is reported as Chrome on Windows; it does not use or identify your installed Edge browser. This integration may break when WhatsApp changes its web client. WhatsApp may log out, restrict, or suspend accounts using unofficial clients or automation. Use at your own discretion. This project does not bypass QR authentication, hide automation, defeat access controls, or use a public tunnel. Keep the app on localhost.

Connect returns as soon as the sidecar starts initializing WhatsApp Web; the UI polls status while Chromium loads, so a slow startup is not reported as a sidecar-unavailable error. If Chromium times out during initialization, the sidecar makes one recovery attempt with a fresh local session and requests a new QR code. This clears the saved linked-device login, so scan the new QR with your phone. If it continues to fail, check `.run/whatsapp-web-error.log`, confirm `web.whatsapp.com` is reachable, close other Chromium-heavy apps, and restart with `stop.ps1` and `start.ps1`.

Run backend checks with `cd backend; .\.venv\Scripts\python.exe manage.py test apps.assistant.tests`. Build the frontend with `cd frontend; npm run build`.

## AI drafting, approvals, and safety

Set `OPENAI_API_KEY` in `backend/.env`, then explicitly turn on message sharing in Personalisation. The incoming message and recent non-sensitive context are sent to OpenAI only after consent. API credentials stay on the backend. No fallback text is generated when AI is unavailable.

Approval mode is the default. Automatic replies are disabled by default and require the global automation switch, a selected Smart/Automatic mode, AI sharing consent, and explicit per-contact authorization. Excluded contacts, sensitive content, messages needing a human decision, and non-text messages are not automatically replied to. Duplicate message IDs are rejected by the database, messages sent by this account are ignored, and reply-to uniqueness prevents duplicate assistant replies. The emergency stop blocks every assistant send. Keep Approval mode enabled until you have reviewed drafts and deliberately configured automation.

## Features

- QR linking and persistent local session for a personal WhatsApp account.
- Incoming direct messages saved locally and passed through the existing AI draft/approval workflow.
- Contact exclusions, per-contact reply modes, allowed categories, and custom instructions.
- Approval, Smart, and Automatic modes; safety filters, working hours, delay, and emergency stop.
- Duplicate event protection and outgoing-message loop prevention.
- Local dashboard for conversations, contacts, drafts, approvals, personalization, automation, and connection status.
- SQLite by default and optional PostgreSQL through `DATABASE_URL`.

## API routes

- `GET /api/health/`, `GET /api/dashboard/`
- `GET|POST /api/contacts/`, `PATCH|DELETE /api/contacts/{id}/`
- `GET /api/conversations/?q=...`, `POST /api/conversations/{id}/send/`
- `POST /api/messages/ingest/`, `POST /api/messages/{id}/draft/`, `PATCH /api/messages/{id}/edit/`, `POST /api/messages/{id}/approve/`, `POST /api/messages/{id}/reject/`
- `GET|PUT /api/preferences/`, `GET|PUT /api/automation/`
- `GET /api/whatsapp/status/`, `POST /api/whatsapp/connect/`, `POST /api/whatsapp/disconnect/`

Manual send requests require an `Idempotency-Key`. An ambiguous provider/network failure must be checked in WhatsApp before a manual retry. Never commit `.env` files or `.run/bridge-token`. This is a local single-user development app, not an internet-facing multi-user service.
