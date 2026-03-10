# Signal Bot

A dockerized Signal bot built with:

- signalbot
- signal-cli-rest-api
- PostgreSQL
- Alembic
- SQLAlchemy async

It expects messages in this format:

*decimal* *decimal* *word*

Example:

2312.123 123123.12312333 word

The bot validates the message and replies with either:
- a success response, or
- a detailed validation error list.

## Local development

### 1. Create local env file

```bash
cp .env.example .env

docker-compose stop && docker-compose up -d
docker-compose run --rm bot alembic revision --autogenerate -m "migration name"
docker-compose logs container_name --tail=500
docker compose run --rm bot alembic upgrade head
```

## Signal account setup

This project supports two Signal account setups:

1. **Use an existing Signal account as a linked device**
2. **Register a separate Signal account for the bot**

Choose **one** approach and keep your `.env` consistent with it. `signalbot` expects `PHONE_NUMBER` to be the number of the account that `main` actually uses. In the documented `signalbot` setup flow, QR linking is done first and the server is then restarted in `json-rpc` mode for normal bot runtime. `signal-cli-rest-api` also documents that `normal` mode is the slowest and `json-rpc` mode is usually the fastest. :contentReference[oaicite:0]{index=0}

### Common prerequisites

Create a local env file and set the database values you want:

```bash
cp .env.example .env
```

At minimum, make sure these values are present:
```bash
SIGNAL_SERVICE=main:8080
POSTGRES_DB=bot
POSTGRES_USER=user
POSTGRES_PASSWORD=YOUR_PASSWORD
DATABASE_URL=postgresql+asyncpg://user:YOUR_PASSWORD@postgres:5432/bot
LOCAL_UID=1000
LOCAL_GID=1000
```

Start infrastructure and apply database migrations:

```bash
docker compose up -d postgres main
docker compose run --rm bot alembic upgrade head
```

## Option 1: use an existing Signal account through QR linking

Use this approach when you already have a working Signal account and are okay with the bot using that same account as a linked device. Signal’s linked-device flow requires opening Signal on the phone, going to Settings → Linked devices, and scanning the QR code shown by the new device. After linking, the phone does not need to stay online, linked devices unlink after 30 days of inactivity, and there is a limit of 5 linked devices per phone.\
When to use this:
- You want to reuse an existing Signal account
- You can scan a QR code from the Signal mobile app
- You do not need a separate Signal identity for the bot

Important limitation\
If Signal is only running inside an emulator on the same PC and you cannot practically scan the QR code, this approach is usually not convenient. In that case, use Option 2 instead. This follows directly from Signal’s linked-device process, which requires the mobile app to scan the QR code.

Steps\
Set the real existing Signal number in .env and start main in normal mode for first-time linking:
```bash
PHONE_NUMBER=+YOUR_REAL_SIGNAL_NUMBER
SIGNAL_API_MODE=normal
```

Start the Signal API:
```bash
docker compose up -d main
```
Open the QR endpoint in your browser:
```bash
http://127.0.0.1:8080/v1/qrcodelink?device_name=local-bot
```

In the Signal app on your phone:
1) open Settings
2) open Linked devices
3) choose Link New Device
4) scan the QR code

The QR endpoint and the linked-device steps are the documented first-time flow for signal-cli-rest-api and signalbot.
After linking succeeds, switch .env to json-rpc mode:
```bash
SIGNAL_API_MODE=json-rpc
```
Restart the API container:
```bash
docker compose up -d --force-recreate main
```
Confirm that main is healthy and has loaded the linked number:
```bash
curl http://127.0.0.1:8080/v1/about
docker compose logs -f main
```

The signalbot docs note that the logs should show the linked number being found, and /v1/about can be used to confirm the server mode.

Start the bot:
```bash
docker compose up -d --build
```

## Option 2: register a separate Signal account for the bot

Use this approach when you want the bot to have its own Signal identity, or when QR linking is not practical. Signal registration still requires a phone number, and the number must be able to receive an SMS or a verification call. signal-cli-rest-api exposes REST endpoints to register a number and verify it with the received code.

When to use this:

- You do not want to use your personal Signal account as the bot
- You cannot conveniently scan the QR code
- You have access to a temporary or separate number for testing

# Temporary testing numbers

For temporary testing, one option used during development was SMSPool.net, where a temporary phone number was purchased just for test registration. Treat this as a short-lived testing aid, not as a durable production identity.

Steps:

Set the separate bot number in .env:
```bash
PHONE_NUMBER=+YOUR_TEMP_NUMBER
SIGNAL_API_MODE=json-rpc
```
Start the Signal API:
```bash
docker compose up -d main
```
Register the number by SMS:
```bash
curl -X POST -H "Content-Type: application/json" \
  'http://127.0.0.1:8080/v1/register/+YOUR_TEMP_NUMBER'
```
Or register by voice call:
```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"use_voice": true}' \
  'http://127.0.0.1:8080/v1/register/+YOUR_TEMP_NUMBER'
```
These registration endpoints are documented by signal-cli-rest-api.

If registration says captcha is required, solve the captcha first.

Open:

https://signalcaptchas.org/registration/generate.html

After solving the captcha, copy the captcha token (failed request in developers console, not including signalcaptcha://) and retry registration:
```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"captcha":"PASTE_CAPTCHA_TOKEN_HERE"}' \
  'http://127.0.0.1:8080/v1/register/+YOUR_TEMP_NUMBER'
```

The captcha requirement and request format are documented in the project examples.

Verify the number with the code you receive:
```bash
curl -X POST -H "Content-Type: application/json" \
  'http://127.0.0.1:8080/v1/register/+YOUR_TEMP_NUMBER/verify/123-456'
```
If the account already has a Signal PIN, include it:
```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"pin":"YOUR_SIGNAL_PIN"}' \
  'http://127.0.0.1:8080/v1/register/+YOUR_TEMP_NUMBER/verify/123-456'
```
 
Confirm that the API container is healthy:
```bash
curl http://127.0.0.1:8080/v1/about
docker compose logs -f main
```
Start the bot:
```bash
docker compose up -d --build 
```