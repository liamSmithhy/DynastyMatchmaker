# Licensing and data use

**Current status: free, personal, non-commercial use only. No money is charged
for this tool and none should be until the item below is resolved in writing.**

## The open blocker

Sleeper's public API is free for **non-commercial** use. Commercial use requires
contacting Sleeper for licensing.

This project therefore ships as a free tool. Every feature is enabled — there is
no paid tier, no locked functionality and no billing code — because a paid tier
built on this data would breach Sleeper's terms until they grant permission.

That is a licensing question, not an engineering one. No amount of code changes
it, and shipping a paid product first and asking later would be the wrong order.

### What to ask Sleeper for

1. Written permission for commercial use of the read-only public API, or the
   terms of a commercial licence.
2. Confirmation of acceptable request volume. This tool is deliberately frugal:
   the ~5MB `players/nfl` payload is cached 24h, league and roster reads 15
   minutes, and transactions 6 hours. A twelve-manager league costs a handful of
   requests per day, not per user per page view.
3. Confirmation that displaying Sleeper-sourced league data back to the managers
   of that same league is acceptable, since that is all this tool does.

### Until then

- Do not charge for access.
- Do not resell or redistribute Sleeper data.
- Keep attribution visible in every user-facing surface.

## Data sources and attribution

Both sources must be credited wherever output is shown.

| Source | Use | Terms to confirm before charging |
| --- | --- | --- |
| [DynastyProcess](https://github.com/dynastyprocess/data) (Tan Ho, Joe Sydlowski) | Player and pick values | Confirm the repository licence permits commercial resale of derived values |
| [Sleeper API](https://docs.sleeper.com) | League, roster, pick and transaction data | Non-commercial only; commercial use requires contacting Sleeper |

This tool reads only. It never writes to a Sleeper league, never proposes or
executes a transaction on a user's behalf, and never asks for a password — it
takes a public username and reads public league data through the documented
public endpoints. There is no OAuth flow and no credential storage anywhere in
this codebase.

## This project's own code

The code in `src/`, `scripts/` and `tests/` is released under the MIT licence
(see `LICENSE`). That licence covers the code only. It does not and cannot grant
rights over the third-party data the code fetches at runtime, which remains
governed by the terms above.
