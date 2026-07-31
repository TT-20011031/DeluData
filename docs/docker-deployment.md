# Docker Deployment (Local + Cloud)

## Topology

- `8020`: visitor experience frontend + `/api/experience/*`
- `8021`: admin frontend + platform frontend (`/platform/`) + `/api/*`
- Internal services: `mysql`, `redis`, `ingestion-worker`

## 1) Prepare env files

```bash
# local
cp backend/.env.docker.local.example backend/.env.docker.local

# cloud
cp backend/.env.docker.cloud.example backend/.env.docker.cloud
```

Fill real values in the copied file:

- `DASHSCOPE_API_KEY`
- `DB_ENCRYPTION_KEY`

## 2) Start services

### Local (GPU)

```bash
BACKEND_ENV_FILE=./backend/.env.docker.local docker compose up -d --build
```

### Cloud (CPU)

```bash
BACKEND_ENV_FILE=./backend/.env.docker.cloud docker compose up -d --build
```

## 3) Access URLs

- Visitor: `http://<host>:8020`
- Admin: `http://<host>:8021`
- Platform: `http://<host>:8021/platform/`

## 4) Health checks

```bash
curl http://<host>:8020/health
curl http://<host>:8021/health
```

## 5) Database migration from local

```bash
# export local mysql
mysqldump -h 127.0.0.1 -P 3306 -u root -p DeluData > DeluData.sql

# import into docker mysql
docker exec -i deludata-mysql mysql -uroot -prootpassword DeluData < DeluData.sql
```

## 6) Notes

- For production domain and HTTPS, place a reverse proxy or load balancer in front of this stack.
- Do not commit real env files with secrets.
