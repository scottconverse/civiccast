# Security and privacy {#ch-security}

This chapter explains how to manage staff access, limit network exposure, protect resident data, and verify CivicCast packages. It covers roles, tokens, first-admin recovery, HTTPS, the public and staff APIs, retention, signatures and checksums. Complete first setup on the station computer and follow the [network access guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/network-access.md) before making the resident portal available remotely.

## Invalid sign-in and setup requests

Malformed setup, sign-in and recovery requests return field locations and safe
validation messages without echoing submitted passwords, recovery codes or validator
context. Do not attach real credentials to a support report. Ordinary incorrect
credentials still use the existing authentication and rate-limit responses.

## Before you start

- You need Administrator rights on the station computer, and the Setup admin role in the console for the staff-token API calls.
- Most commands below need the CivicCast command-line program. The installer does not add it to PATH, so use the full path: `<INSTDIR>\runtime\Lib\site-packages\bin\civiccast.exe` (`<INSTDIR>` is the folder CivicCast was installed to). In this chapter `$cc` stands for that path.
- Token commands need the database address in the `DATABASE_URL` environment variable. Read it from the registry value `HKLM\SOFTWARE\CivicCast\Native\DatabaseUrl` in an elevated PowerShell window, use it only in that window, and never write it to a file or a ticket, because it contains the database password:

```powershell
$cc = "<INSTDIR>\runtime\Lib\site-packages\bin\civiccast.exe"
$env:DATABASE_URL = (Get-ItemProperty 'HKLM:\SOFTWARE\CivicCast\Native').DatabaseUrl
```

## The security model in one page

- The application and database listeners stay on loopback. Remote HTTPS is available through the supported Caddy configuration, which separates the public resident hostname from a staff hostname restricted to configured CIDRs. Do not expose the application listener directly; see the [network access guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/network-access.md).
- Anyone who can sign in to the console is a **staff user** with one or more of five roles.
- Staff sign in with a username and password at `/operator/#/login`; the API returns a bearer token. Other staff API actions require `Authorization: Bearer <token>` and the assigned role.
- Residents use the **public** parts of the API, which need no sign-in.
- The installer configures the service as the restricted virtual account `NT SERVICE\CivicCastSupervisor`. Existing default LocalSystem installations migrate their profile and CivicCast credentials before the account changes. See the [restricted service identity guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/restricted-service-identity.md).

## Roles

CivicCast has five staff roles. A token carries one or more of them. The details of which screens each role opens are in [Appendix F](#app-roles) and in [Chapter 2](#ch-signing-in).

| Role id | Label in the console |
| --- | --- |
| `setup_admin` | Setup admin |
| `meeting_operator` | Meeting operator |
| `records_clerk` | Records clerk |
| `publish_operator` | Publish operator |
| `support_admin` | Support admin |

Rules the code enforces:

- For lifecycle and environment API tokens, the scope words `admin` and `operator` are aliases that grant all five roles. Console sessions use the roles assigned to the signed-in account.
- An API token with no scopes has no roles.
- A protected staff request without a token gets HTTP 401 with `Staff identity is required for this action.` The username/password login route is the exception and issues the session token.
- A request with a token that lacks the needed role gets HTTP 403 with `This action requires one of these CivicCast roles: <role ids>.`
- Setup admins assign roles to staff accounts in **Staff accounts**; CLI-issued database tokens receive scopes when issued.

## Manage staff tokens

A **bearer credential** is a long secret string that proves an identity to the staff API. The operator console uses session credentials for people; programs and scripts use database or environment tokens. There are three kinds.

| Kind | Looks like | Where it is stored | How you retire it |
| --- | --- | --- | --- |
| Console session | Created when a first administrator or staff account signs in | `station-state.json`, as a salted hash | Expires after 12 hours; sign out, admin revocation, or eviction at the 20-session cap also ends it |
| Database token | `ccst_<id>_<secret>` | Database. Only a PBKDF2-SHA256 hash (210,000 iterations) plus a SHA-256 fingerprint is kept; the secret is shown once. | `civiccast token revoke` or `rotate` |
| Environment token | `ccenv1_` followed by 43 characters | The `CIVICCAST_STAFF_TOKENS` environment variable | Remove it from the variable and restart the service |

### Issue a database token

1. Open an elevated PowerShell window and set `$cc` and `DATABASE_URL` as shown above.
2. Run:

```powershell
& $cc token issue --operator-id clerk-jones --display-name "Pat Jones" --scopes records_clerk,publish_operator --json
```

`--operator-id` and `--display-name` are required. `--scopes` takes a comma-separated list of role ids or the aliases above; its default is `operator`, which is all five roles. Give people only the roles they need. `--save-keyring` stores the token in the Windows credential store under the service name `civiccast.staff-token`. `--json` prints machine-readable output.

3. Copy the secret that is printed. It is printed once and cannot be shown again. Deliver it to its owner through a channel you trust, not through the same ticket that names the person.
4. Check it works. In PowerShell, on the station:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/staff/auth/me -Headers @{ Authorization = "Bearer <the token>" }
```

You should see the operator's name and roles.

### List, revoke and rotate

- `& $cc token list` shows token metadata (never secrets). Add `--json` for scripts.
- `& $cc token revoke <token_id> --reason "left the clerk's office"` makes the token fail from then on. `--reason` defaults to `operator-requested` and is saved in the audit record. `<token_id>` is the public id shown by `token list`.
- `& $cc token rotate <token_id>` revokes that token and issues a replacement for the same operator with the same scopes. It prints the new secret once. `--save-keyring` saves it to the credential store.

The database records audit events for `issued`, `used`, `revoked` and `rotated`.

> **Known issue (beta.11):** A database token issued before the fingerprint was introduced cannot be matched. If `token list` shows a token that no longer authenticates after an upgrade, rotate it.

### Environment tokens

Generate one with `& $cc token generate-env`. To use it, add it to `CIVICCAST_STAFF_TOKENS` in the service environment (see [Chapter 12](#ch-operations)) in the form `token:operator_id:Display Name:role[,role]`, with several entries separated by semicolons (`;`), and restart the service. Roles are required: an entry with no role stops the station from starting.

> **Known issue (beta.11):** On a station that has its database (every native station), the code checks database and console tokens first and accepts an environment token only when `CIVICCAST_STAFF_TOKENS_FALLBACK_WITH_DB=1` is also set in the service environment. The code treats that as a recovery setting. Without it, an environment token is rejected with `Invalid staff bearer token.` Prefer database tokens (`token issue`).

> **Warning:** The server cannot revoke an environment token. The console's **Sign out** only makes that browser forget it. To retire one, remove it from the variable and restart the service, which takes the channels off the air.

### Failed attempts are limited

A wrong token counts against the client's address: 10 failures in 60 seconds by default (`CIVICCAST_AUTH_RATE_LIMIT` and `CIVICCAST_AUTH_RATE_LIMIT_WINDOW_SECONDS`). After that the server answers HTTP 429 with a `Retry-After` header, and the console shows `Too many failed attempts to authenticate with the staff API. Wait N seconds, then try again.` A request with no token at all does not count. A valid token still works while the limit is active. The counts live inside the running process and reset when it restarts. CivicCast's own code ignores forwarded-address headers, so it counts failures against the address the web server reports. The web server (uvicorn) is started with its default settings, which replace that address with the `X-Forwarded-For` value when the connection comes from `127.0.0.1`. In a test with uvicorn 0.47 (not the shipped 0.51.0) we saw exactly that. So behind a reverse proxy on the same computer, failures are counted against the proxy's address if the proxy sends no such header, and against the visitor's address if it does.

> **Known issue (beta.11):** The memory the limiter uses to remember addresses has no upper bound (audit finding B-010). Keep the staff hostname restricted to approved CIDRs and retain bearer authentication and role checks; see the [network access guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/network-access.md).

## Understand sign-in, setup and recovery

### First setup and the recovery kit

Open `/operator/#/setup` on the station computer for **First setup**. Creating the first admin there makes:

- the first-admin username and password (the password must have at least 12 characters), and
- a **recovery kit**: 8 one-time recovery codes.

The **Save kit** button downloads a plain-text file that contains the admin password and all 8 codes. The console blocks navigation and sign-out until the kit is confirmed.

> **Warning:** Treat the recovery kit like a master key. Print it and keep it in a locked drawer or a safe, or keep it in your password manager. Do not leave it in the Downloads folder, a shared drive or an e-mail.

The first administrator signs in at `/operator/#/setup` on the station computer. Staff accounts use the separate `/operator/#/login` page, including on the staff hostname from an approved CIDR when Caddy mode is configured. If the first-admin password is lost, **Use recovery code** burns one of the 8 codes and sets a new password; it takes two clicks and warns you each time. Other valid sessions remain signed in. Staff account creation, role assignment and session administration are covered in the [staff accounts guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/staff-accounts.md).

### What the server enforces

- **First setup and first-admin sign-in/recovery are local-only.** The routes under `/api/setup/*` check that the request came from the station computer. The generated Caddy configuration blocks these routes on both remote hostnames. Staff account sign-in uses `/api/staff/auth/login` and can be reached on the CIDR-restricted staff hostname; the public hostname does not forward staff routes.
- Station-local first-admin and staff sessions expire after **12 hours**. At most 20 console sessions are retained; newer sessions can evict the oldest. A Setup admin can list and revoke sessions, and staff password reset, disable or role changes revoke that account's sessions. See the [staff accounts guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/staff-accounts.md).
- The two API calls `POST /api/staff/installer/sessions/revoke-others` and `POST /api/staff/installer/recovery-kit/regenerate` (Setup admin) end the other console sessions and make a new kit (new codes; the old codes stop working at once). The console has buttons for both: **Sign out other sessions** and **Regenerate recovery kit** in the Security card of the Station Profile screen, visible to the Setup admin role.
- For a lost password with no usable recovery codes, use the local administrator procedure below. Do not use `CIVICCAST_ALLOW_FIRST_ADMIN_RESET`: it reruns setup and can replace station settings.

### Administrator password reset

The computer's owner or an authorized local Windows administrator can reset the existing first administrator without the old password or recovery codes. This is an offline maintenance operation, not a web API. It preserves the administrator's username, station settings, PostgreSQL database, recordings and other media. It revokes every first-admin console session and all old recovery codes. Separately issued staff API tokens are unchanged; retire those separately if compromise is suspected.

1. Schedule a short outage and close any manually launched CivicCast server. On the station computer, open **PowerShell → Run as administrator**.
2. Stop the supervisor and run the installed command (replace `<INSTDIR>` with your installation folder):

   ```powershell
   Stop-Service CivicCastSupervisor
   & "<INSTDIR>\runtime\python.exe" -I -m civiccast.cli admin reset-password
   ```

3. Enter the new password twice at the hidden prompts (12–256 characters). Never put it in command arguments, environment variables, a script or a ticket. Cancelling or mismatching the prompts changes nothing.
4. After success, run `Start-Service CivicCastSupervisor`, sign in with the displayed existing username and new password, then open **Station Profile → Security → Regenerate recovery kit**. Save the new kit safely; all previous recovery codes are invalid.

The command checks the actual elevated Windows token, the stopped supervisor's supported service identity, and its registered environment. It holds the supervisor's exclusive Windows mutex throughout recovery, so service starts and another recovery cannot write concurrently. It targets the protected `station-state.json` from the service environment or an absolute local `CIVICCAST_STATION_STATE_PATH`; the invoking user's profile and environment are ignored. Relative, network, linked and junction paths are refused. Custom service identities are not supported by this recovery procedure.

Before changing credentials it creates an ACL-protected `*.before-admin-reset.bak` beside the state file, then replaces the state atomically. The backup contains old credential hashes and session hashes: keep it restricted, do not attach it to support requests, and remove it after confirming access and your normal station backup. Restoring it also restores the old credentials and sessions. A missing or corrupt state file is refused, not reinitialized. If recovery reports a failure, retain the backup, correct the reported condition and retry; restart the supervisor when maintenance is complete. No database password or DPAPI secret is read; the new password is never echoed, logged or stored in plaintext.

The first-admin setup routes still rely on loopback at the application. A custom reverse proxy on the station can make remote requests appear local, so it must refuse every `/api/setup/*` path. The supported Caddy configuration does this explicitly on both hostnames and limits staff-host forwarding to configured source CIDRs. Do not put another proxy or CDN in front of Caddy without separately verifying the address restriction. The Beta 10 audit's Host/Origin observation (E-006) remains historical; complete first setup on the station computer and use the generated Caddy configuration for remote access.

## Know what the station exposes to the network

### What listens, and where

| Service | Bind address | Port | Notes |
| --- | --- | --- | --- |
| Control plane (web API, console, portal) | `127.0.0.1` by default; `127.0.0.1` or `::1` in Network Settings | 8000 by default | Loopback-only upstream; it cannot bind directly to a LAN or public interface. Caddy is the supported remote HTTPS edge. |
| Postgres | `127.0.0.1` | first free of 5432, 5433, 5434, 5435, 5544 | Password logins only (`scram-sha-256`). No TLS. |
| Ollama | `127.0.0.1` | 11434 | Environment `OLLAMA_NO_CLOUD=1` is set for it. |

The application upstream remains loopback-only. In Caddy mode, the public hostname serves resident pages and public routes; a separate staff hostname forwards requests only from configured CIDRs, with bearer and role checks still required. See the [network access guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/network-access.md).

### What the firewall rule opens

The installer adds a Windows Firewall rule named **CivicCast (Native) Portal/API (TCP 8000)**. It is an inbound allow rule for TCP port 8000, limited to the program `<INSTDIR>\runtime\python.exe`, enabled on **all** firewall profiles (domain, private and public) and with no restriction on the remote address.

The TCP 8000 firewall rule does not make the loopback-only application listener directly reachable from other computers. Caddy listener and firewall exposure are configured separately; open only the addresses and ports allowed by station policy. The broad installer rule remains a historical Beta 10 audit finding (E-009); review it with your Windows administrator rather than publishing port 8000.

Outbound traffic is not restricted by CivicCast.

### Other web protections

- **CORS** (which web sites may call the API from a browser) is deny-all by default. Allow specific sites with `CIVICCAST_CORS_ALLOWED_ORIGINS` as a list of exact origins. A wildcard `*` is refused as a startup error.
- Responses carry a `Content-Security-Policy` that begins `default-src 'self'` and adds narrower rules (for example `object-src 'none'` and `frame-ancestors 'none'`), plus `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`. A response that already sets one of these headers keeps its own value.
- Native stations set `CIVICCAST_LAN_ONLY_STATION=1`. With it, the API documentation pages `/docs` and `/redoc` are off, and `/openapi.json` needs a staff token.
- `CIVICCAST_AUTH_ACK=1` only silences a start-up warning about authentication. It does not change who can sign in.

## Know the public and staff parts of the API

The web API has distinct groups of paths. Which group a path is in decides who may call it.

| Path group | Who can call it | What it is |
| --- | --- | --- |
| `/api/staff/*` | Staff hostname from an allowed CIDR in Caddy mode, plus a staff bearer token and role | Staff sign-in and operator actions. The public hostname does not forward this path group. |
| `/api/setup/*` | Station computer only; some post-setup actions also require a staff token | First setup, first-admin sign-in and recovery. Caddy refuses these paths. |
| `/api/public/*` | Anyone who can reach the public hostname or local listener | Resident features. |
| `/api/webhooks/stripe` | Stripe, with a signature | Payment events. |
| `/media/*` | Anyone | Media files for the portal. |
| `/ap/*` | Anyone | ActivityPub (federation) endpoints. Federation mode is `disabled` unless `CIVICCAST_ACTIVITYPUB_MODE` is set. |
| `/health`, `/api/health`, `/api/version`, `/api/hardware` | Anyone | Health, version and hardware summaries. |

The staff middleware gates every path that begins with `/api/staff/`. The public asset lists show only assets that are published.

> **Known issue (beta.11):** There is no setting that turns off the public contributor routes (`/api/public/contribute`); the router is always included. Anyone who can reach the port can use them. See the limitations section.

> **Known issue (beta.11):** `GET /api/hardware` needs no sign-in and returns the computer's host name (audit finding B-012). Treat the host name as public.

## Configure HTTPS and remote access

The control plane speaks plain HTTP to a loopback-only upstream. CivicCast provides a generated Caddy configuration for the HTTPS edge; it separates resident pages from staff access, blocks `/api/setup/*`, and restricts the staff hostname to configured CIDRs. Follow the [network access guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/network-access.md): review the generated Caddyfile, configure DNS and the approved firewall edge, and test from both allowed and denied networks. Do not expose port 8000 directly or put an unverified proxy/CDN in front of Caddy. The application does not install or manage the Caddy service.

Do not publish `/api/public/contribute*` while the upload limitations below apply. The staff bearer token and role checks remain required even on the approved staff network.

**Internal certificates are a different thing.** The station contains a local certificate authority for mutual TLS between its own internal programs (the identities `civiccast-api` and `civiccast-worker`). Certificates last 90 days and show `rotation_due` in the last 30 days. Renew one with `& $cc cert rotate <identity>` (add `--json` for scripts). The root folder is `CIVICCAST_CERT_ROOT`, default `.civiccast\certs` under the service account's home. These certificates are not the HTTPS certificate for a browser and cannot be used as one.

## Protect subscribers and viewers

### Viewer analytics

- The portal's analytics beacons are stored **without** an IP address, a session identifier or a viewer identifier. A stored event has an event id, event name, time, app target, channel id, content id and a short list of safe properties (such as country, region, state, device, platform and caption language). String values are truncated to 160 characters.
- A beacon is accepted for storage only when `CIVICCAST_PUBLIC_ANALYTICS_KEY` or `CIVICCAST_PUBLIC_ANALYTICS_ALLOWED_ORIGINS` is set. Otherwise the beacon is accepted and dropped, which is "telemetry off". A beacon larger than 16,384 bytes is refused.
- Stored events are kept for up to 366 days (`CIVICCAST_ANALYTICS_RETENTION_DAYS`, range 1 to 366).

### Subscribers

- A subscriber's e-mail address is stored encrypted with AES-GCM. Confirmation and unsubscribe links carry signed tokens.
- The encryption key and the token secret come from the pair `CIVICCAST_SUBSCRIBE_TOKEN_SECRET` and `CIVICCAST_SUBSCRIBE_ENCRYPTION_KEY` (set both or neither). If you set neither, the station creates a durable file `subscribe-secrets.json` (in `CIVICCAST_CONFIG_DIR` or `.civiccast` under the service account's home).
- Alert destination URLs and signing credentials are stored separately in `alert-credentials.json`. On Windows, both local secret files are created with a restricted file ACL before any secret bytes are written. When older files have broader access, CivicCast replaces them atomically with the same bytes; if it cannot secure the replacement, the operation fails and leaves the original file in place.
- The e-mail confirmation token is not shown in the API reply.

> **Warning:** Back up that key. A database restored without it keeps encrypted addresses that nobody can read ([Chapter 12](#ch-operations)).

> **Known issue (beta.11):** The confirmation token for a webhook subscription **is** returned in the API reply (a known gap).

> **Known issue (beta.11):** By default the e-mail, webhook, YouTube, Internet Archive and NAS providers are mock or local providers. Real ones are chosen with `CIVICCAST_PROVIDER_<KIND>=real`, where the kind is `MAIL`, `WEBHOOK`, `YOUTUBE`, `INTERNET_ARCHIVE` or `LOCAL_NAS` (see [Appendix C](#app-settings)); choosing a real provider without its credentials makes the station fail fast at start-up. Until you switch the mail provider, no subscriber e-mail is actually sent.

## Retain and delete data

| Data | Kept | Source |
| --- | --- | --- |
| Live-caption working audio | Processed chunks are deleted. Whistle retains at most 42 waiting five-second segments per channel (210 seconds), plus in-flight inputs and the segment being written; runtimes without that processing grace use a 12-segment ceiling. Native live operation does not run the optional age-based retention sweep. | `captions/tap_worker.py`, runtime wiring |
| Live-caption text and delivery tracking (native beta.11 runtime) | Rolling 300 seconds and at most 512 cues; no permanent per-cue review rows or evidence WAVs | `captions/stabilize.py`, `egress/caption_feed.py` |
| Recorded-caption review and original recordings | Existing recording/review workflow remains unchanged; live working limits do not delete archived tracks or recordings | `captions/vod.py`, recording workflow |
| Historical beta.10 caption-retention audit | The beta.10 audit found that `caption-retention-audit.jsonl` was not rotated. Native beta.11 live captioning does not enable the optional age-based retention sweep. | `captions/retention.py`, `captions/tap_worker.py` |
| Analytics events | Up to 366 days (default; range 1 to 366) | analytics store |
| Playout health telemetry | Not trimmed automatically; trim with `civiccast egress trim-health --older-than-days N` | CLI |
| Staff token audit events, alert history, as-run records | No deletion setting found | not found |

Live captions are off by default, and `CIVICCAST_CAPTION_TAP=off` forces them off. The native runtime separates transient live caption work from recorded-caption review. It does not make the live worker or broadcast readiness depend on archive-wide review/evidence retention discovery. No live review/evidence archive is created by ordinary live captioning; this does not change recorded captions or original recordings.

> **Historical finding (published Beta 10):** Audit findings B-003, B-004, B-005 and B-009 recorded unbounded live-caption evidence growth. The current native live path does not run that archive sweep, writes no per-cue evidence, deletes processed chunks and bounds the queued audio and rolling caption history. These source behaviors do not replace package-specific verification; see the release record matching the installed package.

Where residents' own data may be requested for deletion, we found no console screen or command that deletes a subscriber or a viewer's records. Ask the coder to confirm before you promise a deletion process.

## Check what you install: signing and checksums

- **The installer** is signed with Authenticode through Azure Trusted (Artifact) Signing. The policy file `CODE_SIGNING_POLICY.md` names the signer as Scott Converse. Windows SmartScreen may still show a reputation warning for a new publisher. To check a download:

```powershell
Get-AuthenticodeSignature .\CivicCast-Setup.exe | Format-List Status, SignerCertificate
Get-FileHash .\CivicCast-Setup.exe -Algorithm SHA256
```

The status should read `Valid`. Compare the hash with the line for that file in `SHA256SUMS.txt` on the matching release page. Check that the installer signature is valid and its hash matches the published `SHA256SUMS.txt`. Replace the file name with the real one.

- **Sidecar.** The release also has a `setup.exe.sidecar.json` file with checksum metadata. It is plain metadata and is not separately signed (`attestation` is `null`). `civiccast installer verify-package --artifact <file> --sidecar <file> [--json]` checks a file against it.
- **Packs.** The large components (`.ccpack` files) carry ed25519 signatures that the installer verifies. The installer also checks a SHA-256 for each file, rejects names containing `..` and rejects reparse points (Windows links). The pack index is pinned to the product version, so an older pack cannot be offered to a newer product.
- **Build downloads** are pinned by hash, and the application's Python packages are installed with `--require-hashes`.
- **No sigstore/cosign.** The owner decided against it (decision record ADR 0022).

> **Known issue (beta.11):** All packs are signed by one key that has no expiry and no rotation procedure (audit finding E-012). If a pack were ever to be signed by a stolen key, the installer would accept it. Download beta releases only from the project's GitHub release page.

> **Historical beta.10 audit finding (repository settings):** The 2026-10-02 audit reported that signing secrets were not restricted to a protected environment, action versions used mutable tags and `main` was not protected. Those observations describe repository settings at the audit date, not their current state. For every release, trust only the official release page, verify the installer's signature and compare its hash with the release checksum file.

## Historical beta.10 security audit findings

The entries below summarize the whole-repository audit dated 2026-10-02 (`audit-lite-whole-repo-beta10-2026-10-02`). They preserve that audit's findings and severities; later beta.11 code and installer changes mean they are not a current release review. Current configuration and safety instructions appear in the operating chapters above. Finding numbers are the audit's.

| Finding | What is wrong | What you do |
| --- | --- | --- |
| B-001 (Critical) | An anonymous visitor's contributor upload is accepted and written to disk **before** the size limits are checked, so a flood of uploads can fill the disk. | Keep the public contributor portal off the internet. Do not forward `/api/public/contribute*` at a proxy. Use the upload limits (`CIVICCAST_CONTRIBUTOR_MAX_UPLOAD_BYTES`, `CIVICCAST_CONTRIBUTOR_UPLOAD_DIR_MAX_BYTES`, `CIVICCAST_CONTRIBUTOR_UPLOAD_PER_IP_MAX_BYTES`, `CIVICCAST_CONTRIBUTOR_UPLOAD_RATE_LIMIT` default 10, `CIVICCAST_CONTRIBUTOR_SUBMISSION_RATE_LIMIT`) as a second layer. Watch free space. |
| E-001, B-002 | A webhook subscription can be pointed at an internal address (server-side request forgery) and its signing secret is guessable. | Webhook delivery happens only when `CIVICCAST_PROVIDER_WEBHOOK=real`. Leave it unset unless you need it, and keep the public subscribe routes off the internet. |
| E-006 | The loopback setup routes do not check `Host` or `Origin`. | Finish first setup before the computer is used for anything else. Do not browse the web from the station computer. |
| Proxy pitfall (design, not an audit finding) | A custom reverse proxy on the same computer can make requests appear to come from loopback. | Use the generated Caddy rules: refuse `/api/setup/*`; allow staff paths only on the separate CIDR-restricted staff hostname. |
| E-007 (historical Beta 10 audit) | The audit recorded that service and children ran as LocalSystem. | Current installer source configures the restricted virtual account; an isolated Windows Sandbox run with the current source overlay verified profile and CivicCast credential migration, virtual-account startup, and a healthy API with the current schema. This is development-source evidence, not qualification of a rebuilt or signed installer or production cutover. See the [service identity guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/restricted-service-identity.md). |
| B-006 | Any signed-in local user can wedge the supervisor's control pipe. Playout is not affected, but the supervisor's status, start, stop and restart verbs stop answering. | Allow only administrators to log in to the station. Windows `sc.exe` still starts and stops the service. |
| E-003 | The installer opens log and URL paths through `cmd.exe /C start`; an `&` in such a path can run a command. Exploiting it needs earlier write access to the installer's state. | Do not run the installer from a folder whose name or content an untrusted person controls. |
| E-004, E-005 | The dependency vulnerability scan checks a lock file that differs from what ships (37 of 81 pins differ), and an allow-list entry has expired. | Treat the shipped Python packages as not independently scanned. Watch the release notes for updates. |
| E-009 | The firewall rule is broader than needed. | See "What the firewall rule opens". |
| E-011 | The embedded Python is 3.12.10. The audit states, from memory and not verified online, that this was the last 3.12 build with binary installers, so the shipped interpreter may not get further stdlib security fixes. | Keep the station off untrusted networks until a later release replaces it. |
| E-012, E-002 | One pack-signing key with no expiry; build secrets and branch not protected. | See "Check what you install". |
| B-010 | The failed-login limiter has no memory bound. | Keep staff access on the separate hostname restricted to approved CIDRs; retain bearer authentication and role checks. |
| B-012 | `/api/hardware` shows the host name to anyone. | Treat the host name as public. |
| B-019 | `civiccast model set-provider-key --key <value>` puts the key on the command line, visible to other programs in the process list. | Supply the key in the `CIVICCAST_PROVIDER_API_KEY` environment variable instead. |
| B-003, B-004, B-005, B-009 | Historical beta.10 caption data growth (see retention). | Beta.11 removes automatic live review/evidence accumulation and bounds live working state; verify the candidate before deployment. |
| A-001, A-008 | The audit reported a program-change watchdog gap and no free-space guard on the 60 GB cache. Beta.11 includes a rollover watchdog and cache free-space guard; their code behavior is described in [Chapter 12](#ch-operations). | The audit findings are historical; current runtime reliability still depends on exact-package evidence. |
| C-001 | Automated tests were red in about 110 places at the time of the audit. | Historical audit result only; it does not describe the beta.11 test state. |

> **Note:** The beta.10 audit also discussed operational items such as alerts with no destination, the disaster-recovery drill's `pg_dump` dependency and log rotation. See their current operating guidance in [Chapter 12](#ch-operations).

> **Warning:** Keep the staff hostname restricted to approved networks and bearer-authenticated accounts, do not publish contributor uploads while their listed limits apply, and run only the signed installer from the official release page.

## Package status and verification

This chapter describes the published Beta 20 edition. Use the [Beta 20 verification record](https://github.com/scottconverse/civiccast-native/blob/main/docs/releases/v1.0.0-beta.20-verification.md) for package checks; the [Beta 18 record](https://github.com/scottconverse/civiccast-native/blob/main/docs/releases/v1.0.0-beta.18-verification.md) remains historical. A historical Beta 15 field report found the installed `CivicCast Native.exe` unsigned while the outer setup was signed; this does not establish the status of another package. See the [deferred field follow-up](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/beta17-deferred.md).

### Report a security problem

Report a suspected vulnerability privately, as `SECURITY.md` in the repository describes. Do not open a public issue. The policy states that a report is acknowledged within 72 hours.

## If it did not work

| What you see | Cause | What to do |
| --- | --- | --- |
| `No DATABASE_URL configured` or a database connection error from `civiccast token ...` | `DATABASE_URL` not set in this window | Set it as shown in "Before you start". |
| `Staff identity is required for this action.` (401) | No `Authorization: Bearer` header | Send the header. |
| `This action requires one of these CivicCast roles: ...` (403) | The token lacks the role | Issue a token with the role. |
| `Too many failed attempts to authenticate with the staff API. Wait N seconds, then try again.` (429) | 10 bad tokens in 60 seconds from one address | Wait. Check the token. |
| A token you issued is rejected | Revoked, rotated, or issued before the fingerprint change | `token list`, then `token rotate`. |
| Console says **First setup can only be done from the station computer itself** | You opened the console from another computer | Use the station computer or a remote-desktop session on it. |
| **You were signed out** | The oldest of 20 sessions was evicted, or the station's sign-in state was reset | Sign in again. |
| `Invalid admin username or password.` | Wrong credentials | Use the recovery kit's password or one of the 8 codes. |
| A signed installer shows `NotSigned` or a hash that does not match `SHA256SUMS.txt` | Corrupt or altered download | Do not run it. Download again from the release page. |

## Related

- [Signing in and finding your way around](#ch-signing-in)
- [Running it day to day](#ch-operations)
- [Configuring the station](#ch-configuration)
- [Cable headend, streaming, CDN, federation, emergency alerts, the API](#ch-integrations)
- [Troubleshooting matrix](#ch-troubleshooting)
- [Appendix C: settings](#app-settings), [Appendix F: roles](#app-roles), [Appendix H: measured evidence](#app-evidence)

<!-- SOURCES: civiccast/auth/middleware.py, tokens.py, roles.py, store.py, rate_limit.py, cors.py, security_headers.py, keyring_store.py; civiccast/cli.py:2030-2220 (token commands); ops/docs-sprint/inventory/generated/cli.md (token, cert, installer verify-package, egress trim-health); civiccast/installer/station_state.py (sessions, recovery kit, PBKDF2); civiccast/installer/router.py:1195-1305 (loopback setup guard); ops/docs-sprint/inventory/screens/_shell-signin-and-session.md, setup.md, _shell-navigation-and-roles.md; civiccast/app.py:2093-2175 (LAN-only, /docs off), 2323-2450 (/health, analytics cap); civiccast/native/supervisor/children.py (host 127.0.0.1, Postgres bind); civiccast/native/provision/conf.py (postgresql.conf, pg_hba); civiccast/apps/installer/src-tauri/src/native_service_registration.rs:2714-2830 (firewall rule); civiccast/native/station_runtime.py; civiccast/certs/authority.py; civiccast/subscribe/service.py, secrets.py; civiccast/platform/providers.py; civiccast/analytics/models.py, store.py; civiccast/app_platform/router.py (analytics ingest); civiccast/schedule/router.py (public assets); civiccast/captions/retention.py; ops/docs-sprint/inventory/generated/api.md (path groups); docs/ops/staff-route-protection.md, docs/ops/local-ca-mtls.md (topics only); SECURITY.md; CODE_SIGNING_POLICY.md; docs/install/windows-release-trust.md; docs/releases/v1.0.0-beta.10-verification.md; ops/beta10-oversight/audits/audit-lite-whole-repo-beta10-2026-10-02.md and slices/slice-E-security.md, slice-B-captions-app.md, slice-A-egress.md -->
