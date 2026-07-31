# Science Experience Backend

## Docker Compose quick start

```bash
docker compose up -d --build
```

- Visitor gateway: `http://localhost:8020`
- Admin gateway: `http://localhost:8021`
- Platform frontend: `http://localhost:8021/platform/`

## Process split

- `admin-backend`: admin/config/museum/platform/science-admin APIs
- `experience-backend`: `/api/experience/*` guest interaction APIs
- `ingestion-worker`: async ingestion queue worker

## Local vs cloud env

`docker-compose.yml` uses:

```bash
BACKEND_ENV_FILE=./backend/.env
```

You can switch to dedicated env files:

```bash
# local (GPU)
BACKEND_ENV_FILE=./backend/.env.docker.local docker compose up -d --build

# cloud (CPU)
BACKEND_ENV_FILE=./backend/.env.docker.cloud docker compose up -d --build
```
