# Methodology & Architecture

The target is a Vue 3 Single Page Application (SPA) that talks to a same-origin `/api/v2` backend. There is no documented public API; everything in this repository was recovered using the **Capture → Spec → Replay → Refresh** methodology and confirmed against live browser traffic.

---

## 1. The Core Lifecycle

```
[Browser Session] ──(CDP Capture)──> [site_spec.json]
                                           │
                                     (Validation)
                                           ▼
[Live API] <──(Auto-Refresh / Retries)── [DuelClient]
```

1. **Capture**: Attach over Chrome DevTools Protocol (CDP) to an authenticated browser instance. Capture cookies, local storage UUIDs, and network telemetry without attempting headless login.
2. **Spec**: Normalize endpoints, token types, error codes, and recovery ladders into a declarative `site_spec.json`.
3. **Replay**: Issue typed requests matching browser headers, device fingerprints, and origin expectations.
4. **Refresh**: Automatically detect token expiry or Cloudflare challenge and execute bounded recovery steps.

---

## 2. Endpoints Inventory

### Public Bootstrap (No Authentication Required)
| Endpoint | Notes |
|---|---|
| `GET /api/v2/metadata?uuid=` | Issues initial session cookie, 169 feature flags, `turnstile_sitekey` |
| `GET /api/v2/games` | Game catalogue (`start` / `filter` parameters) |
| `GET /api/v2/slots` | Slot catalogue |
| `GET /api/v2/metadata/exchange-rates` | Live crypto/fiat FX rates |
| `GET /api/v2/trade/metadata/payment-methods` | Deposit methods |
| `GET /api/v2/trade/crypto/withdraw/methods` | Withdrawal methods (read-only listing) |
| `GET /api/v2/user/zero-edge-rakeback/status` | Rakeback tier status |

### Authenticated Endpoints (Requires Active Session)
| Endpoint | Notes |
|---|---|
| `POST /api/v2/metadata/socket-token` | Realtime streaming JWT (~30s TTL) |
| `POST /api/v2/auth/login` · `POST /api/v2/auth/logout` | Session authentication lifecycle |
| `GET /api/v2/user` · `GET /api/v2/user/settings` · `GET /api/v2/user/kyc` | User profile & KYC status |
| `GET /api/v2/client-seed` | Provably-fair active seed pair |

### Realtime Streaming (Socket.io)
Realtime events use **socket.io v4** at `https://roulette.duel.com` (engine path `/s`, websocket-only transport, namespace `/livebetfeed`).
- Handshake query string carries `uid` and short-lived `socket_token`.
- Client emits `identify` with `{uid, authorizationToken, signature, uuid}`.
- **Bot-Gate**: Handshakes from non-browser clients are answered with `{"code":3,"message":"Bad request"}`. This project surfaces the refusal cleanly via `duel-api betfeed` rather than spoofing signatures.

---

## 3. Provenance & Drift Tripwire

| Item | Recorded Value |
|---|---|
| **Bundle URL** | `/assets/index-Cs9Avd2r.js` |
| **Bundle SHA-256** | `518cc628…` |
| **Spec Version** | `0.1` |
| **Tripwire Tool** | `duel-api spec-check` |

When duel.com deploys a new frontend bundle, the bundle hash changes. `duel-api spec-check` downloads the current HTML entry point, hashes the script tags, and flags drift immediately before requests fail.

---

## 4. Request Chokepoint & Money Gates

Writes flow through a strict chokepoint in `DuelClient.request()`:

| Request Class | Policy | Behavior |
|---|---|---|
| **Reads** (GET/HEAD/OPTIONS) | Unrestricted | Permitted |
| **Session Lifecycle** | Controlled | Allowed (login is captcha-gated, socket-token refreshes JWT) |
| **Settings / Seeds** | Opt-In | Requires `--yes` on CLI or `allow_writes=True` in Python |
| **Money Paths** | Gated | Refused by default; `dice-bet` requires 4-step confirmation; `autobet` requires `--confirm` + `--security-token` |
| **Withdrawal / Deposit** | Blocked | Unconditionally refused at the client layer |
