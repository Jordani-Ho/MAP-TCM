# Alpha HTTP API

All payloads are JSON. The server binds to localhost by default.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness and safety mode |
| GET | `/api/v1/overview` | Synthetic demo encounter and audit overview |
| POST | `/api/v1/encounters` | Create an encounter after active consent |
| POST | `/api/v1/encounters/{id}/observations` | Add a sourced fact or draft |
| POST | `/api/v1/encounters/{id}/assessments` | Add a physician assessment |
| POST | `/api/v1/encounters/{id}/review` | Move draft to doctor-reviewed |
| POST | `/api/v1/encounters/{id}/sign` | Sign a reviewed encounter |
| POST | `/api/v1/encounters/{id}/withdraw` | Withdraw without deleting history |

The API is intentionally narrow. It demonstrates contract and workflow enforcement, not production authentication or clinical interoperability.
