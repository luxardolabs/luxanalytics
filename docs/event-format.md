# LuxAnalytics event format

The contract between a client (the Swift SDK, [luxardolabs/luxanalytics-swift](https://github.com/luxardolabs/luxanalytics-swift), or any other) and this server's ingest API. It describes what the server does today; the machine-readable form is the OpenAPI document (`/openapi.json`, served when `DEBUG=true`, so on dev only).

## Endpoints and authentication

| Endpoint                           | Authentication                                                                       |
| ---------------------------------- | ------------------------------------------------------------------------------------ |
| `POST /api/v1/events/{project_id}` | DSN: HTTP Basic, username = the app's public id, empty password. **The SDK's path.** |
| `POST /api/v1/events/public`       | Legacy DSN form, kept for older SDK builds.                                          |
| `POST /api/v1/events/`             | HMAC-SHA256 (below).                                                                 |

A DSN reads `https://PUBLIC_ID@HOST/api/v1/events/PROJECT_ID`. Each app registered in the dashboard (Apps) gets one. There is no API-key mode.

**HMAC.** Three headers: `X-Key-ID` (the key id in the server's `HMAC_KEYS`), `X-Timestamp` (Unix seconds) and `X-HMAC-Signature` (lowercase hex). The signature is `HMAC-SHA256(secret, body_bytes + timestamp_string)`, over the body exactly as sent, so over the **compressed** bytes when the body is compressed. The request is refused (401) when `X-Timestamp` is more than `EVENT_TIMESTAMP_FUTURE_TOLERANCE` (60 s) from the server's clock in either direction. That is replay protection, and it means an HMAC client's clock must be within a minute of real time; the DSN path has no such check.

## The body

JSON, in one of three shapes:

- one event object;
- `{"events": [ ... ]}`;
- a list of events.

A request carries at most **1000** events. Validation is **all-or-nothing per request**: one invalid event makes the whole request a 422 and nothing is stored, so a client should validate events before batching them.

### An event

| Field        | Type                                | Rules                                                                                                                                                                                                                                                                                                           |
| ------------ | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`         | string, optional                    | The client's id for the event: the **idempotency key** (below). 1–64 characters, no NUL or lone surrogate. Any other value (a number, an empty or longer string) is accepted but not used for deduplication.                                                                                                    |
| `name`       | string, required                    | 1–255 characters after trimming; no NUL or lone surrogate. Use `snake_case` (the dashboard titles it: `app_launched` → "App Launched").                                                                                                                                                                         |
| `timestamp`  | string, required                    | ISO 8601. A trailing `Z` or an offset; without one it is read as UTC. A time more than 60 s **ahead** of the server, or before 2000-01-01, is stored as the receive time (a wrong device clock is clamped, not rejected). Any other past time is stored as sent, so events queued offline keep their real time. |
| `user_id`    | string, optional                    | Up to 255 characters; no NUL or lone surrogate.                                                                                                                                                                                                                                                                 |
| `session_id` | string, optional                    | Up to 255 characters; no NUL or lone surrogate. Screen transitions and session views group by it.                                                                                                                                                                                                               |
| `metadata`   | object of string → string, optional | No NUL or lone UTF-16 surrogate in keys or values.                                                                                                                                                                                                                                                              |

### Idempotency

Set `id` once, when the event is created, and resend it unchanged on every retry. The server keeps one row per `(app, id)`: an event whose `id` this app already sent, or that appears twice in one request, is acknowledged and not stored again. Different apps never collide. Events without a usable `id` are always stored.

### Metadata: device context and properties

These metadata keys are **device context**. The server stores them in their own columns and keeps a per-device record, and they never appear among the event's properties:

`device_id`, `device_model`, `device_type`, `system_version` (stored as the OS version), `app_version`, `build_number`, `screen_resolution`, `locale`, `timezone`, `is_testflight` (`"true"` or anything else).

`platform` is also read from metadata (default `"ios"`). Every other key is an event **property**, stored as sent and shown in the dashboard's explorer.

## Compression

`Content-Encoding: deflate` means the **zlib** format (RFC 1950) wrapping a DEFLATE stream, as RFC 9110 §8.4.1.2 defines it. The Swift SDK sends that from 1.1.0, above its size threshold (1 KB by default). Raw DEFLATE without the zlib wrapper (RFC 1951) is still accepted from SDK 1.0.2 and earlier; new clients must not send it.

A body that decompresses past the request size limit (10 MB) is refused with 413. One that is neither format is refused with 400.

## Responses

| Status | Meaning                                                                                                                                                                                 | A client should                                     |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| 200    | `{"status": "success", "events_received": N, "duplicates": D, "message": "…"}`. `events_received` counts every event in the request; `duplicates` how many of them were already stored. | mark the batch sent                                 |
| 400    | Malformed JSON, the wrong body shape, or an undecodable compressed body.                                                                                                                | drop the batch                                      |
| 401    | Missing or wrong credentials (public id, HMAC signature or timestamp).                                                                                                                  | drop it; fix the configuration                      |
| 403    | HMAC signature does not match.                                                                                                                                                          | drop it                                             |
| 404    | Unknown `project_id` ("Invalid project").                                                                                                                                               | drop it; fix the DSN                                |
| 413    | Body too large, before or after decompression.                                                                                                                                          | split the batch                                     |
| 422    | An event failed validation; the whole request is rejected.                                                                                                                              | drop the batch (or fix and resend the valid events) |
| 429    | Rate limited, with `X-RateLimit-*` headers.                                                                                                                                             | retry after the reset                               |
| 5xx    | Server error.                                                                                                                                                                           | retry with backoff                                  |

## Event names and keys the dashboard reads

Any event name and property is stored and explorable. These drive the built-in views:

| View              | Events and keys                                                                                                                                                                                                        |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Screens, journeys | `screen_viewed` with `screen`. Transitions, entry and exit screens and dwell time are derived on the server from each session's screen order, so `previous_screen` is not needed, and `journey_*` events are not read. |
| Interactions      | `button_tapped` (or `button_tap`) with `button` and `screen`                                                                                                                                                           |
| Launches          | `app_launched` with `launch_type`, `launch_count`, `is_authenticated`                                                                                                                                                  |
| Features          | any event with `feature`                                                                                                                                                                                               |
| Errors            | `error_occurred` with `error_type`, `error_message`, `screen`; `authentication_attempt` with `auth_method`                                                                                                             |
| Performance       | `performance_measured` with `operation`, `duration_ms` (milliseconds), `success` (`"true"`/`"false"`)                                                                                                                  |
| Feedback          | `feedback_submitted` with `feedback_category`, `feedback_content`                                                                                                                                                      |

The screen key is `screen`; `screen_name` is not read.
