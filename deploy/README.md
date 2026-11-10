# Docker deployment: PostgreSQL + Cloudflare Tunnel

## Topology

```text
Browser --HTTPS--> Cloudflare --outbound tunnel--> cloudflared
    --> web:8080 (nginx) --> app:8000 (Gunicorn/Django)
                              | PostgreSQL + Redis
                              | shared uploads
                         worker (Django-Q ORM broker)
```

There are **no published host ports**, host networking, or router port forwards.
`web:8080` and `app:8000` are container-only listeners. PostgreSQL and Redis use an
internal network; app/worker have separate outbound access for SMTP/integrations.
Nginx bridges the backend and tunnel networks. Cloudflared cannot reach the DB.

PostgreSQL 17 uses the username and password you supply. As with the standard
PostgreSQL image, this is the bootstrap database owner (a PostgreSQL superuser on
fresh initialization), so keep the database on its private network. Redis
shares the cache; Django-Q retains its PostgreSQL ORM broker. One `app-init` job
runs migrations and collects static assets before app/worker start. Gunicorn and
Django-Q run as UID/GID 10001. The build-only uv installer is not in the runtime;
production pins pydyf to the API compatible with WeasyPrint 62.

## 1. Prepare directories and the root .env BEFORE Docker

Requirements: Docker Engine, Docker Compose, Python 3 and a Cloudflare-managed
hostname. Run from the repository root **as your normal user, not with sudo**:

```bash
python3 scripts/init_docker.py
```

The script:

- Creates `data/` as your user, with mode 0700, before Docker is invoked.
- Creates every data bind-mount source directory on the host.
- Creates root **`.env`** from **`.env.example`**, generates only the Django
  secret, and sets its permissions to 0600.
- Preserves existing `.env` values and adds missing configuration keys. Database
  passwords are never generated, imported, replaced or rotated by setup.
- Creates an empty `data/secrets/cloudflared-token` if missing.
- Refuses to run as root or use a top-level `data/` owned by another UID.
- Never imports, deletes or modifies the old `backend/db.sqlite3` database.

**Every Compose bind mount sets `create_host_path: false`.** Skipping setup causes
Docker to fail on a missing source instead of silently creating root-owned paths.
The secret file must also already exist. You may copy `.env.example` to `.env`
manually first; still run the initializer before starting Docker.

### Ownership after startup

The **top-level `data/` remains owned by your host user**. Specific service data
must be owned by the container UID that writes it:

- PostgreSQL owns `data/postgres/pgdata/`.
- Redis owns its data directory/files.
- The one-shot init container sets only `data/media/` and `data/static/` ownership
  to UID/GID 10001 so the non-root application can write uploads/static assets.
- `data/backups/` and `data/secrets/` stay host-user-owned.

Repeated setup runs do not recursively chown or chmod service directories. Do not
run `sudo chown -R` over `data/`: that can break PostgreSQL and Redis. If an older
manual setup created a root-owned top-level directory, inspect it and repair
**only that parent** before running setup:

```bash
sudo chown "$(id -u):$(id -g)" data
```

## 2. Configure .env and the tunnel

Edit root `.env`:

```dotenv
PUBLIC_HOSTNAME=school.your-domain.com
POSTGRES_USER=school
POSTGRES_PASSWORD=your-chosen-password
```

Use only a hostname (no scheme/path/port).

**Do not write a database URL or Redis URL. Both services are included.**
Compose automatically connects Django to PostgreSQL at `db:5432` (database
`school`) and Redis at `redis:6379/1`. **The only database fields are
`POSTGRES_USER` and `POSTGRES_PASSWORD`.** Enter your own password; setup never
generates one. Compose refuses to start with a blank username or password.
Keep usernames to simple letters, digits and underscores.

If the database is already initialized, use its current login credentials unless
you also rotate the actual PostgreSQL role password (see below). Earlier versions
of this deployment used a separate application login: use that existing role and
its password in these two fields. This configuration change does not alter roles
or reset existing data. Do not delete `data/` to switch configuration.

Configure SMTP if sending real email; the default console backend only logs messages.

Docker Compose automatically reads root `.env`: **no `--env-file` is needed**.
Compose explicitly uses PostgreSQL, Redis and Django production settings, ignoring
local-development `DATABASE_URL`, `CACHE_URL` and `DEBUG` defaults. The image never
contains `.env`; only the explicitly listed environment variables are passed in.

In the Cloudflare Zero Trust dashboard:

1. Create a remotely managed Cloudflared tunnel.
2. Put **only its token** in `data/secrets/cloudflared-token` (not the full command).
3. Add a published application route for the same `PUBLIC_HOSTNAME`.
4. Choose **HTTP**, URL **`web:8080`**. Leave HTTP Host Header overrides unset or
   set them to the public hostname.
5. Redirect HTTP visitors to HTTPS at the Cloudflare edge.

Nginx sets the trusted HTTPS proxy header; Django enables secure cookies and
explicit hosts/CSRF origins. Do not expose this origin through an untrusted route.
Cloudflared requires outbound DNS and port 7844 (UDP QUIC / TCP HTTP/2).

The token is mounted as a file, not passed as a command-line token. It is 0644 so
cloudflared's non-root UID can read the bind-mounted secret; the parent directories
are private to your host user. If an editor changes its permissions:

```bash
chmod 700 data data/secrets
chmod 644 data/secrets/cloudflared-token
```

## 3. Build and start

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps -a
docker compose logs --tail=100 app-init app web worker cloudflared
```

`app-init` exiting with code **0** is expected. Open the configured HTTPS hostname
once the tunnel connects. There is no localhost URL published by this stack.

Before a real tunnel token is ready, validate only the internal services:

```bash
docker compose up -d --build app worker web
docker compose exec -T web wget -qO- http://127.0.0.1:8080/healthz/
```

Create the initial administrator interactively:

```bash
docker compose exec app python manage.py createsuperuser
```

Demo accounts are not created automatically. For an explicitly isolated test
instance only, `docker compose exec app python manage.py seed_demo_data --force`
creates known demo logins. Replace/deactivate them before exposing the tunnel.

## Persistence and backups

```text
.env                          deployment configuration (ignored, mode 0600)
data/                         host-user-owned parent (ignored, mode 0700)
  secrets/cloudflared-token   dashboard tunnel credential
  postgres/pgdata/            PostgreSQL database and WAL
  redis/                     Redis append-only persistence
  media/                     uploads
  static/                    collected/manifest-processed static assets
  backups/                   explicit PostgreSQL backups
```

Both `.gitignore` and `.dockerignore` exclude `.env` and the entire `data/` tree.
No named volumes are used. Data survives image rebuilds and container recreation,
including `docker compose down -v`. Deleting `data/` destroys the deployment state.
Temporary files and font caches use disposable tmpfs. Diagnostic stdout/stderr
logs use Docker's rotated `local` driver (3 × 10 MB per service); use a log collector
for long-term retention. This single-host setup is not an HA deployment.

Create a consistent custom-format database dump:

```bash
bash scripts/backup_docker.sh
```

It writes under `data/backups/`, renaming a temporary file only after success.
Also back up `data/media/`, `.env` and the tunnel secret. Pause writes for a
coordinated DB/files snapshot and store encrypted backups off the machine.
Never treat a copy of a live PostgreSQL directory as a consistent backup.

For restoration, back up the current state first, stop the application/tunnel,
restore into a fresh database using `pg_restore` in the DB container, retain the
application role, and verify migrations/counts/upload references/logins before
reopening access. Destructive replacement is intentionally not automated.

Changing passwords in `.env` does not rotate existing PostgreSQL roles: update
the role securely with `ALTER ROLE`, then update `.env` and recreate app/worker.
Never delete the DB to rotate passwords. For tunnel-token rotation, replace the
file and recreate cloudflared so its bind mount points at the new file.

## Uploaded-file security

Nginx serves static assets and intentionally public logo/gallery directories,
not a blanket `/media/` alias. Private pupil submissions, imports, finance files
and message attachments must not become anonymous downloads.

**Current limitation:** other legacy direct `/media/…` links, including avatars,
return 404 until protected object-level download endpoints are implemented. Files
are persisted. Do not remove the deny rule to make them public. Gallery files
are treated as public; never put confidential images there.

## Updates and validation

Back up first, review migrations and use a maintenance window:

```bash
docker compose stop cloudflared web app worker
docker compose build app
docker compose up --no-deps --force-recreate --exit-code-from app-init app-init
# Proceed only after app-init exits successfully.
docker compose up -d app worker web cloudflared
```

Pin tested image digests and lock Python dependencies for fully reproducible
releases. PostgreSQL major upgrades require pg_upgrade or dump/restore; do not
point another major version at the existing data directory. Startup healthchecks
do not provide ongoing orchestration; restart policies restart exited processes.

```bash
python3 -m unittest discover -s deploy/tests -v
docker compose config --quiet
docker compose exec app python manage.py check --deploy
```

The external tunnel cannot be tested without the actual hostname/token.
Cloudflare token-file reference:
https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/cloudflared-parameters/run-parameters/#token-file
