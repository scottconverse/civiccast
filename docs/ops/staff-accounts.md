# Station staff accounts

The setup administrator can add local staff accounts, assign CivicCast roles,
disable sign-in, reset passwords, and review or revoke station-local sessions.
The existing first administrator remains the recovery account: its password
and one-time recovery codes continue to use First Setup, and the staff screen
does not let an operator demote or disable it.

Staff records and station-local session hashes live in the existing
`station-state.json` file. Passwords are stored as salted PBKDF2-SHA256 hashes;
bearer tokens are returned once at login and only salted token hashes are
persisted. Keeping these records in station state makes them part of the
station's portable state backup. The account and session endpoints never
return password hashes, token salts, or bearer secrets.

New first-admin and staff sessions expire 12 hours after issue. Login is
failure-rate-limited. A password reset, account disable, or role change ends
that account's saved sessions; the next login receives the current role set.
The station refuses a change that would remove its final active setup
administrator. The first administrator remains a recovery path even when the
staff account list is otherwise empty.

The API contract is:

- `POST /api/staff/auth/login` accepts `username` and `password`, returning a
  one-time `access_token`, identity, roles, issue time, and expiry.
- `GET /api/staff/auth/accounts` lists the first administrator and staff
  accounts. `POST` creates a staff account; `PATCH /{operator_id}` updates its
  display name, roles, or enabled state; `POST /{operator_id}/reset-password`
  replaces a password.
- `GET /api/staff/auth/sessions` lists safe session metadata. `DELETE
  /{session_id}` revokes that station-local session. The existing
  `POST /api/staff/auth/sign-out` continues to end the caller's session.

All account and session administration requires the `setup_admin` role. The
operator console exposes staff sign-in at `/operator/#/login`, keeps the existing
first-admin flow and recovery at `/operator/#/setup`, and shows Staff accounts in the
Setup sidebar to `setup_admin` users. In Caddy mode, the staff API is reachable
only on the CIDR-restricted staff hostname; the public hostname does not proxy
staff APIs, and `/api/setup` remains local-only.
