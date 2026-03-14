# Signal Bot

A dockerized Signal bot built with:

- signalbot
- signal-cli-rest-api
- PostgreSQL
- RabbitMQ
- Alembic
- SQLAlchemy async

It expects messages in this format:

*decimal* *decimal* *target description*

Example:

48.563123 39.8917 tank

The bot validates the message, queues TAK delivery, and replies with either:
- a success response, or
- a detailed validation error list.

### Requirements
Objective: Create a Signal bot to send geolocation and target information to an ATAK (Android
Team Awareness Kit) client.

1. Design a Python-based Signal bot that can send messages like:
   "48.567123 39.87897 tank" (longitude, latitude, target description).
2. Format the message in Cursor on Target (CoT) protocol.
3. Send the formatted CoT message to an ATAK client (https://www.civtak.org/).
4. Ensure that the message is displayed in the ATAK client interface.

## Notes
1. I've implemented it in a bit different order - *latitude longitude description* - since it's more common when coordinates start with latitude. Also, it makes much more sense that the tank from the example was on the Russian border instead of somewhere in Azerbaijan. In case it's wrong, it can be easily changed in `validation.py:81`.
2. Must note that lat, long, and target description are too small an amount of information for proper target displaying. It would be good to know at least friend/enemy/civilian and stale time. Also, for a real project it would be good to replace the scoring system, which tries to guess the CoT type based on target description, with some ML model, since it does not cover cases when the user makes mistakes in words or forgets the correct wording for a target.
3. Since it's a military-related app, I was focused on the durability and security of the application. That's why my implementation was based on a TAK server instead of a direct connection to an ATAK client.
4. Due to the same durability reasons, I've implemented two additional loops: one for retries - in case a message was not sent due to some error, and one for replay - resending messages while they are still actual according to the stale parameter, to prevent cases when a mark was not delivered to someone because of client-side issues, such as an internet connection issue.

### Prerequisites
docker
docker-compose
libnss3-tools


### Developer commands
```bash
make lint
make test
make test-fast
make licenses
make logs-bot
make migrate
make migration m="describe change"
make shell
make dbshell
```


## Signal account setup

This project supports two Signal account setups:

1. **Use an existing Signal account as a linked device**
2. **Register a separate Signal account for the bot**

Choose **one** approach and keep your `.env` consistent with it. `signalbot` expects `PHONE_NUMBER` to be the number of the account that `signal-cli-rest-api` actually uses. In the documented `signalbot` setup flow, QR linking is done first and the server is then restarted in `json-rpc` mode for normal bot runtime.

### Common prerequisites

Create a local env file and set the database values you want:

```bash
cp .env.example .env
```

At minimum, make sure these values are present:

```bash
SIGNAL_SERVICE=signal-cli-rest-api:8080
RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
POSTGRES_DB=bot
POSTGRES_USER=user
POSTGRES_PASSWORD=YOUR_PASSWORD
DATABASE_URL=postgresql+asyncpg://user:YOUR_PASSWORD@postgres:5432/bot
# May be different for non-ubuntu system
LOCAL_UID=1000
LOCAL_GID=1000
```

Start infrastructure.

```bash
docker compose up -d postgres rabbitmq signal-cli-rest-api
```

## Option 1: use an existing Signal account through QR linking

Use this approach when you already have a working Signal account and are okay with the bot using that same account as a linked device. Signal’s linked-device flow requires opening Signal on the phone, going to Settings → Linked devices, and scanning the QR code shown by the new device. After linking, the phone does not need to stay online, linked devices unlink after 30 days of inactivity, and there is a limit of 5 linked devices per phone.

### When to use this

- You want to reuse an existing Signal account
- You can scan a QR code from the Signal mobile app
- You do not need a separate Signal identity for the bot

### Important limitation

If Signal is only running inside an emulator on the same PC and you cannot practically scan the QR code, this approach is usually not convenient. In that case, use Option 2 instead. This follows directly from Signal’s linked-device process, which requires the mobile app to scan the QR code.

### Steps

Set the real existing Signal number in `.env` and start signal-cli-rest-api in normal mode for first-time linking:

```bash
PHONE_NUMBER=+YOUR_REAL_SIGNAL_NUMBER
SIGNAL_API_MODE=normal
```

Start the Signal API:

```bash
docker compose up -d signal-cli-rest-api
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

After linking succeeds, switch `.env` to `json-rpc` mode:

```bash
SIGNAL_API_MODE=json-rpc
```

Restart the API container:

```bash
docker compose up -d --force-recreate signal-cli-rest-api
```

Confirm that signal-cli-rest-api is healthy and has loaded the linked number:

```bash
curl http://127.0.0.1:8080/v1/about
docker compose logs -f signal-cli-rest-api
```

The signalbot docs note that the logs should show the linked number being found, and /v1/about can be used to confirm the server mode.

Start the bot:

```bash
docker compose up -d --build
```

## Option 2: register a separate Signal account for the bot

Use this approach when you want the bot to have its own Signal identity, or when QR linking is not practical. Signal registration still requires a phone number, and the number must be able to receive an SMS. signal-cli-rest-api exposes REST endpoints to register a number and verify it with the received code.

### When to use this

- You do not want to use your personal Signal account as the bot
- You cannot conveniently scan the QR code
- You have access to a temporary or separate number for testing

### Temporary testing numbers

For temporary testing, one option used during development was SMSPool.net. Treat this as a short-lived testing aid, not as a durable production identity.

### Steps

Set the separate bot number in `.env`:

```bash
PHONE_NUMBER=+YOUR_TEMP_NUMBER
SIGNAL_API_MODE=json-rpc
```

Start the Signal API:

```bash
docker compose up -d signal-cli-rest-api
```

Register the number by SMS

Open [signalcaptchas.org/registration/generate.html](https://signalcaptchas.org/registration/generate.html).

After solving the captcha, copy the captcha token (from the failed request in the developer console, not including `signalcaptcha://`) and start registration:

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"captcha":"PASTE_CAPTCHA_TOKEN_HERE"}' \
  'http://127.0.0.1:8080/v1/register/+YOUR_TEMP_NUMBER'
```

Verify the number with the code you receive in SMS:

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
docker compose logs -f signal-cli-rest-api
```

## ATAK setup

If the Signal part is working, but the TAK server is down, messages will be stored in the database and resent later, if they are not outdated.

### Steps

Download the latest version of [TAK Server](https://tak.gov/products/tak-server).
The app was tested on version `5.6.0`.

Unzip it into the project folder `infra/tak`, so `infra/tak` should contain 2 folders - `tak` and `docker`.

Run setup script:\
It will ask for the root password at some point. It's required to give the current user access to autogenerated certificates.\
*WARNING* Import of the certificate into the browser will work only on Chromium-based browsers (tested on Google Chrome). For other browsers, you will need to import `/infra/tak/tak/certs/files/admin.p12` into the browser manually.
```bash
chmod u+x setup.sh && ./setup.sh
```


## CoT event format and type selection

Each valid Signal message is converted into a CoT (Cursor on Target) XML event and sent to the TAK server.

A typical event contains:

- `uid`: a stable unique ID generated from the Signal sender, message timestamp, and original text
- `type`: the TAK object classification chosen from your target phrase
- `how`: set to `h-e`, meaning the report comes from human-entered input
- `time`, `start`, `stale`: timing fields used by TAK clients
- `point`: your latitude and longitude
- `contact callsign`: the target phrase you sent
- `remarks`: debug-style matching information showing how the phrase was interpreted

Example shape:

```xml
<event version="2.0" uid="signal-..." type="..." how="h-e" time="..." start="..." stale="...">
  <point lat="48.563123" lon="39.8917" hae="0" ce="10" le="10" />
  <detail>
    <contact callsign="tank" />
    <remarks>source_target=tank; matched_desc=...; matched_full=...; score=...</remarks>
  </detail>
</event>
```

Main XML elements:

- `event`: the root CoT object. It identifies the report, its classification, and its time window.
- `point`: the geographic position of the report.
  - `lat` and `lon` are taken from the Signal message
  - `hae` is altitude
  - `ce` and `le` are horizontal and vertical error values
- `detail`: extra metadata attached to the event.
- `contact`: human-readable display information for TAK clients.
  In this project, `callsign` is set from the target phrase you sent.
- `remarks`: extra text describing how the bot interpreted your target phrase and which CoT catalog entry it matched.

Important `event` attributes:

- `uid`: stable identifier for the same report across retries and replays
- `type`: TAK classification code chosen from the target phrase
- `how`: set to `h-e` because the report comes from human-entered Signal input
- `time`: when this CoT event was generated
- `start`: when the event becomes valid
- `stale`: when TAK clients should consider the event outdated unless it is refreshed

### CoT object families

CoT `type` values are hierarchical codes. The first letter tells you which broad family the event belongs to, and the rest of the code narrows it down into more specific categories.

The most common families in the catalog are:

- `a-*` - atoms. These are concrete map objects or tracks such as people, vehicles, aircraft, ships, weapons, ground units, and installations. This project currently generates `a-*` types because it is reporting a specific object at a specific location.
- `b-*` - bits. These are supporting map and sensor artifacts rather than tracked platforms. They include imagery, detections, routes, waypoints, map points, grids, and CBRNE-related detection markers.
- `c-*` - capability or functional categories. These describe operational capability areas such as communications, fires, logistics, rescue, and surveillance.
- `t-*` - tasking. These are request or mission-assignment types, for example target, strike, ISR, mensuration, relocation, or update tasks.
- `y-*` - tasking responses and status. These report acknowledgements, completion, failure, approval, execution state, planning state, and other workflow status messages.
- `r-*` - reports for specific hazard or incident taxonomies. In this catalog they are mainly used for detailed CBRNE and chemical-agent report trees.

You may also see special relation entries such as `t-p-f` (`from`) or `t-p-r` (`regarding`). Those are not tracked objects themselves; they describe how CoT events are related to each other.

![atomic_events.png](assets/atomic_events.png)

### How the CoT `type` is calculated

When you send a message like:

```text
48.563123 39.8917 tank
```

the target phrase, here `tank`, is matched against the CoT catalog in `assets/CoTtypes.xml`.
Source of catalog: https://github.com/dB-SPL/cot-types/blob/main/CoTtypes.xml

The bot does this in steps:

1. Normalize the phrase.
   It lowercases the text, removes extra punctuation, and collapses whitespace.

2. Apply aliases for common user terms.
   Examples:
   - `car` -> `civilian vehicle`
   - `drone` -> `drone uav`
   - `soldier` -> `ground unit`

3. Compare the normalized phrase with every catalog entry.
   Each catalog entry has:
   - a CoT code
   - a short description (`desc`)
   - a full description (`full`)

4. Score each possible match.
   The score increases when:
   - your phrase exactly matches the short description
   - your phrase exactly matches the full description
   - your phrase appears inside the description text
   - your phrase shares words with the catalog entry

5. Pick the highest-scoring entry.
   Its CoT code becomes the `type` field in the XML event.

In practical terms, the matcher strongly prefers exact matches, then partial text matches, then word overlap. This makes common target phrases behave predictably while still allowing less exact wording to map to something useful.

If nothing matches well enough, the bot falls back to the generic CoT type:

```text
a-o-G-U
```

So even unusual target phrases are still forwarded to TAK, but they may appear as a generic ground unit instead of a more specific object type.
