# Redaction rules (run in Azure Logic App)

Redaction happens **before** any payload leaves Azure. Implement each rule as a
"Compose" or "Replace" action in the Logic App, applied to every string field
from the KQL result (`message`, `stackTrace`, `requestPath`, etc.).

## Patterns to strip

| Category              | Regex (flavor: .NET / Logic App `replace`)                                           | Replacement     |
| --------------------- | ------------------------------------------------------------------------------------ | --------------- |
| Norwegian fnr / d-nr  | `\b\d{11}\b`                                                                          | `<fnr>`         |
| Email                 | `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`                                     | `<email>`       |
| JWT                   | `eyJ[A-Za-z0-9_\-]+?\.[A-Za-z0-9_\-]+?\.[A-Za-z0-9_\-]+`                             | `<jwt>`         |
| Bearer token          | `(?i)Bearer\s+[A-Za-z0-9._\-]+`                                                      | `Bearer <tok>`  |
| Basic auth            | `(?i)Basic\s+[A-Za-z0-9+/=]+`                                                        | `Basic <tok>`   |
| Connection string     | `(?i)(Server|Data Source|Password|Pwd|User Id|Uid)=[^;"']+`                          | `$1=<redacted>` |
| Azure storage key     | `[A-Za-z0-9+/]{80,}={0,2}`                                                           | `<b64-redact>`  |
| Query string secrets  | `(?i)([?&](?:api[_-]?key|token|access_token|sig|code)=)[^&#\s"']+`                   | `$1<redacted>`  |
| IPv4                  | `\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b`                                             | `<ip>`          |
| Phone (NO)            | `\b(?:\+?47[\s-]?)?(?:\d{2}[\s-]?){3,4}\d{2}\b`                                      | `<phone>`       |

## Allowlist (keep intact)

- exception type (e.g. `System.NullReferenceException`)
- namespaces and class names in stack frames
- HTTP status codes
- correlation ids (UUIDs from App Insights — not tied to personal data)
- timestamps

## Field-level guidance

- **Do not forward** `customDimensions` blindly. Only forward the keys you
  have explicitly reviewed.
- **Do not forward** request bodies, headers, or cookies. If debugging requires
  them, add them to the allowlist deliberately and redact them first.

## Verification

Add a Logic App step that asserts the final outbound JSON does not match any of
the regexes above. If it does, fail the run instead of creating the issue.
