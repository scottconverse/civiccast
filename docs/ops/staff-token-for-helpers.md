# Give an AI or IT helper a staff token, then take it back

Use this when a person or an AI helper needs to call the station's staff API
for a short job. You give the helper its own token. You do not give it your
password, and you do not show it the database address.

You need: the station computer, and a PowerShell window opened with
**Run as administrator**. `<INSTDIR>` is the folder CivicCast was installed to.

```powershell
$cc = "<INSTDIR>\runtime\Lib\site-packages\bin\civiccast.exe"
```

The token commands read the database address from the protected registry value
`HKLM\SOFTWARE\CivicCast\Native\DatabaseUrl` themselves. You do not set
`DATABASE_URL`, and the address is never shown. In a window that is not
elevated, the command stops with "Open PowerShell with Run as administrator".

## 1. Issue the token into a protected file

Give the helper only the roles the job needs (see [Security and
privacy](../manual/src/24-security.md) for the role list). Use a new operator
id per helper, so you can revoke it alone.

```powershell
& $cc token issue --operator-id helper-ai-2026-11 --display-name "AI helper (Nov 2026)" --scopes support_admin --out C:\CivicCast-handoff\helper-token.txt
```

- The secret goes into the file, not on the screen.
- Only Administrators, SYSTEM and you can read the file. Other users and the
  station service cannot.
- The command never replaces a file. If the file exists, pick a new name.
- The output shows the public **token id**. Note it; you need it to revoke.

## 2. Hand it over

Give the helper the file contents through a channel you trust. Then delete the
file:

```powershell
Remove-Item C:\CivicCast-handoff\helper-token.txt
```

The helper sends the token as `Authorization: Bearer <token>` to
`/api/staff/...` routes.

## 3. Revoke it when the job is done

```powershell
& $cc token list
& $cc token revoke <token_id> --reason "helper job finished"
```

From then on every request with that token fails. The revocation and its
reason go into the token audit record. If you lost the token id, `token list`
shows each token's id, operator id and name (never the secret).

To replace a token instead, `& $cc token rotate <token_id> --out <new file>`
revokes the old one and writes the new secret to a new protected file.
