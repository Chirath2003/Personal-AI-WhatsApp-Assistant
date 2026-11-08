# Personal AI WhatsApp Assistant

A local-first personal messaging assistant with a React dashboard and Django REST API. It stores contacts, conversations, drafts and preferences in SQLite. Reply mode defaults to Approval. It supports the official Meta WhatsApp Business Platform Cloud API; no browser automation is used.

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
cd ..
```

### Start and stop

```powershell
.\start.ps1
.\stop.ps1
```

The scripts start the Django API on 127.0.0.1:8000, the approval/automation worker, and Vite on 127.0.0.1:5173. Logs and process IDs are written to the ignored `.run/` folder. The launcher refuses to start if the API or frontend port is already in use. For separate terminal windows, run `python manage.py runserver 127.0.0.1:8000` from `backend` and `npm run dev -- --host 127.0.0.1 --port 5173` from `frontend`; run `python manage.py assistant_worker` from `backend` in a third terminal.

API health: http://127.0.0.1:8000/api/health/.

Run backend checks with `cd backend; python manage.py test apps.assistant.tests`. Build the frontend with `cd frontend; npm run build`.

## WhatsApp Business Platform Cloud API

This integration uses Meta’s official Business Platform Cloud API. It is not a QR login for a regular consumer WhatsApp account. You need a Meta developer app, an eligible Business Platform phone number or test number, an access token with messaging permissions, the phone number ID, app secret, and webhook verify token. Follow Meta’s [developer hub](https://whatsappbusiness.com/developers/developer-hub/) and [Cloud API setup guide](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started/).

Set these variables in `backend/.env`, restart the backend, then select **Connect WhatsApp** in the dashboard:

- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_APP_SECRET`
- `WHATSAPP_VERIFY_TOKEN` (choose a strong random value and enter the same value in Meta webhook setup)
- `WHATSAPP_GRAPH_VERSION` (defaults to `v26.0`; use a version supported by your Meta app)

Set the Meta webhook callback to `https://YOUR_PUBLIC_HOST/api/whatsapp/webhook/`, verify the token, and subscribe to the `messages` field. Meta requires a public HTTPS callback. The local server binds to localhost; do not expose the whole local API or use an unrestricted tunnel. If you set up a reverse proxy for the webhook, expose only the webhook path and keep the dashboard and other API routes private. The callback verifies Meta’s HMAC SHA-256 signature before processing events. Incoming text is supported; other message types are recorded as placeholders for human review.

Cloud API policy, number eligibility and messaging windows are controlled by Meta. This assistant sends plain text only; if the provider rejects a message, check the conversation window or account configuration. An ambiguous network failure is marked failed and is not automatically retried, to avoid duplicate sends.

## AI drafting and privacy

AI drafting is disabled until configured. Set `OPENAI_API_KEY` in `backend/.env`, then explicitly turn on message sharing in Personalisation. The incoming message and recent non-sensitive context are sent to OpenAI only after consent. API credentials stay on the backend. No fallback text is generated when AI is unavailable.

## Implemented workflows

- Local contacts, conversation history, searchable contacts and conversations, and dashboard metrics/activity.
- Incoming message ingestion with duplicate event-ID protection, outgoing-event exclusion, webhook signature validation and local activity records.
- Rules for OTPs/passwords, financial content, emergencies, commitments and likely promotional spam. Sensitive context is filtered before AI calls.
- Consent-gated structured OpenAI analysis and reply drafting, editable/rejectable approvals, and explicit manual replies. The background worker creates drafts in Approval mode only when message sharing is enabled.
- WhatsApp connection status/check, connect/disconnect, signed inbound webhook and user-triggered text sends via Cloud API.
- Contact exclusion, explicit per-contact auto-reply category allowlists, reply modes, and custom instructions. Smart/Automatic modes require a live provider, AI sharing consent, an explicit global enable, and at least one authorized contact/category. The worker only auto-sends a `REPLY` with confidence at least 0.95, `requires_approval: false`, and an explicitly selected greeting, thanks, or casual category. Factual, sensitive, financial, emergency, and commitment-related topics never auto-send. Working hours, reply delay and emergency stop are enforced. All other cases stay for human review.
- SQLite by default and optional PostgreSQL through `DATABASE_URL`.

## API routes

- `GET /api/health/`, `GET /api/dashboard/`
- `GET|POST /api/contacts/`, `PATCH|DELETE /api/contacts/{id}/`
- `GET /api/conversations/?q=...`, `POST /api/conversations/{id}/send/`
- `POST /api/messages/ingest/`, `POST /api/messages/{id}/draft/`, `PATCH /api/messages/{id}/edit/`, `POST /api/messages/{id}/approve/`, `POST /api/messages/{id}/reject/`
- `GET|PUT /api/preferences/`, `GET|PUT /api/automation/`
- `GET /api/whatsapp/status/`, `POST /api/whatsapp/connect/`, `POST /api/whatsapp/disconnect/`
- `GET|POST /api/whatsapp/webhook/` (Meta verification and signed events)

Manual send requests require an `Idempotency-Key`. The local provider send is one-shot; failed/ambiguous sends must be checked in WhatsApp before any manual retry. Never commit `.env` files. This is a local single-user development app; it is not an internet-facing multi-user service.
