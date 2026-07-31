# Mojave specimen review

A shared shortlist for choosing native plants for a Mojave Desert front yard in
Clark County, Nevada. Forty-two species, sorted by the layer they occupy in a
real desert plant community, each with photographs from Wikimedia Commons.
Stamp plants **Plant it / Maybe / Pass**, leave notes, and the shortlist builds
itself.

Live at **https://nv.mbolt.app**.

## The stack

Deliberately small — one binary, one file of state:

| Piece | Choice | Why |
|---|---|---|
| HTTP | Go stdlib `net/http` | Go 1.22+ method+pattern routing covers this app; no framework earns its keep here |
| Assets | `embed.FS` | HTML, 103 photographs and 5 fonts ship inside the binary — no asset volume, no CDN |
| Database | SQLite via `modernc.org/sqlite` | Pure Go, so `CGO_ENABLED=0` and the runtime image is `distroless/static` |
| Auth | GitHub OAuth, hand-rolled | ~60 lines of HTTP, fewer moving parts than a library |
| Sessions | HMAC-signed cookie | Signed, not encrypted — the payload is just a login and an expiry |

Exactly one non-stdlib dependency (`modernc.org/sqlite`).

### Why `modernc.org/sqlite` and not `mattn/go-sqlite3`

`mattn/go-sqlite3` is a cgo binding. Using it means the build container needs a C
toolchain, the binary links against libc, and the runtime image has to match that
libc — which is exactly the alpine/musl-versus-glibc mess this project does not
need. `modernc.org/sqlite` is a pure-Go translation of SQLite, so the build stays
`CGO_ENABLED=0` and the final image can be `distroless/static`. It is slower on
heavy write loads, which does not matter for a shortlist of 42 plants.

## Running locally

```sh
make run          # http://localhost:8080, read-only (no sign-in configured)
make test         # go test ./... -race
```

Read-only is a real mode, not a failure: without OAuth credentials the site
serves and browses normally, and every write returns 401. That is what lets it
deploy before an OAuth app exists.

To exercise sign-in locally, register a GitHub OAuth app with callback
`http://localhost:8080/auth/callback`, then:

```sh
export GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=...
make run-auth
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ADDR` | `:8080` | Listen address |
| `DB_PATH` | `data/mojave.db` | SQLite file; parent directory is created |
| `BASE_URL` | `http://localhost:8080` | Public origin, used to build the OAuth callback |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | — | OAuth app credentials; unset means read-only |
| `SESSION_KEY` | — | HMAC key for session cookies; unset means read-only |
| `ALLOWED_USERS` | — | Comma-separated GitHub logins allowed to write. **Empty means any GitHub account can write** |

## API

| Route | Auth | Purpose |
|---|---|---|
| `GET /api/state` | none | Whole shortlist plus who you are and whether you may write |
| `PUT /api/plants/{id}` | session + allowlist | Set one plant's verdict and note; last write wins |
| `GET /healthz` | none | Readiness/liveness, pings SQLite |
| `GET /auth/{login,callback,logout}` | — | GitHub OAuth web flow |

Clearing both the verdict and the note deletes the row, so the table only ever
holds plants someone actually stamped.

## Deployment

DigitalOcean Kubernetes (`sfo2`), same cluster as `dnd-dashboard`. Push to
`main` runs `.github/workflows/deploy.yml`: vet and test, build and push
`syntaf/mojave-specimen-review:<sha>` to Docker Hub, then
`helm upgrade --install --atomic -n dnd`.

### Why the `dnd` namespace

This app is unrelated to the D&D dashboard, but it lives in that namespace on
purpose. Everything that makes `*.mbolt.app` work is owned by the
`dnd-dashboard` Helm release:

- the NGINX controller (`ingressClassName: dnd-nginx`) — and it runs with
  `-watch-namespace=dnd`, so it ignores Ingresses anywhere else;
- `external-dns`, which watches cluster-wide with `--domain-filter=mbolt.app`
  and writes the DigitalOcean records automatically;
- `letsencrypt-prod-dnd`, a **namespaced** `Issuer` (this cluster has no
  ClusterIssuers), solving DNS-01 against DigitalOcean.

A separate namespace would need its own ingress controller (and therefore a
second DigitalOcean LoadBalancer, ~$12/month), its own Issuer, and its own DNS
token. Sharing the namespace costs nothing and adds no coupling in the other
direction: this is a separate Helm release that the `dnd` release knows nothing
about.

Nothing needs doing at the registrar or in the DigitalOcean panel — external-dns
creates the `nv` A/AAAA records within about a minute of the Ingress appearing,
and removes them if it goes away (`--policy=sync`).

### Deployment gotchas worth remembering

- **`strategy: Recreate`.** A ReadWriteOnce block volume cannot attach to the old
  and new pod simultaneously, so a rolling update deadlocks.
- **Distinct TLS secret** (`nv-mbolt-cert`). Sharing `dnd-cert` would have the
  two releases fighting over one secret.
- **SQLite pragmas.** WAL plus a 5s `busy_timeout`, because the volume is network
  block storage and latency spikes.
- **`fsGroup: 65532`.** The distroless `nonroot` user needs the PVC to be
  group-writable.

### Required repository secrets

GitHub repository secrets do not carry over from other repos, so all of these
must be added here:

| Secret | Notes |
|---|---|
| `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` | Same values as `dnd-dashboard` |
| `DIGITALOCEAN_ACCESS_TOKEN`, `DO_CLUSTER_ID` | Same values as `dnd-dashboard` |
| `GH_OAUTH_CLIENT_ID`, `GH_OAUTH_CLIENT_SECRET` | From a new GitHub OAuth app, callback `https://nv.mbolt.app/auth/callback` |
| `SESSION_KEY` | Any long random string, e.g. `openssl rand -base64 48` |

Without the last three the deploy still succeeds and the site is browsable and
read-only.

The `deploy` job is gated on a repository **variable**, so the first push does
not fail on missing tokens. Once the secrets are in place, set
`DEPLOY_ENABLED=true` under Settings → Secrets and variables → Actions →
Variables, and the next push to `main` ships.

## Photographs

All photographs come from Wikimedia Commons, resized and re-encoded to WebP.
`data/photos.json` records, for each image, the Commons file page, photographer
and licence. Each plate credits its photographer in the UI, and "Source ↗" links
to the file page for full licence terms.

Every cactus and yucca in this list is a protected species in Nevada — buy
nursery-grown stock, never dig from wild land.
