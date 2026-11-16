"""Embedding Arena orchestrator + dashboard.

Runs inside the cluster, creates the pgvector database and benchmark Jobs on demand,
collects progress reports from workers and streams the whole picture to the browser.
"""
import asyncio
import collections
import os
import time
import uuid
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from catalog import MODELS, slug

NS = os.environ.get("NAMESPACE", "arena")
IMAGE = os.environ.get("ARENA_IMAGE", "docker.io/library/python:3.12-slim")
APP_CONFIGMAP = os.environ.get("APP_CONFIGMAP", "arena-app")
PYTHONPATH = os.environ.get("PYTHONPATH", "/cache/pylib")
PG_IMAGE = os.environ.get("PG_IMAGE", "docker.io/pgvector/pgvector:pg17")
REPORT_URL = os.environ.get("REPORT_URL", f"http://arena-dashboard.{NS}.svc.cluster.local/api/report")
CACHE_HOST_PATH = os.environ.get("CACHE_HOST_PATH", "/var/lib/arena-cache")
DB_DSN = f"postgresql://arena:arena@arena-postgres.{NS}.svc.cluster.local:5432/arena"
STATIC = os.environ.get("STATIC_DIR", os.path.join(os.path.dirname(__file__), "static"))

ACTIVE = {"scheduling", "connecting", "loading", "embedding", "indexing", "querying"}
PHASES = {"connecting", "loading", "embedding", "indexing", "querying"}

try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()
core = client.CoreV1Api()
apps = client.AppsV1Api()
batch = client.BatchV1Api()


class Arena:
    def __init__(self):
        self.settings = {"max_parallel": 2, "threads": 4, "corpus_size": 500}
        self.lock = asyncio.Lock()
        self.events = collections.deque(maxlen=250)
        self.pods = []
        self._pod_seen = {}
        self._db_conn = None
        self._prev_db = None
        self.fresh()

    def fresh(self):
        self.run_id = uuid.uuid4().hex[:6]
        self.state = "idle"
        self.started_at = None
        self.finished_at = None
        self.attempt = 0
        self.db = {"phase": "offline", "seed": None, "dataset": None, "connections": [], "stats": {}}
        self.models = {
            m["id"]: {
                **{k: m[k] for k in ("id", "name", "org", "dim", "params")},
                "slug": slug(m["id"]), "status": "queued", "progress": 0.0, "message": "", "metrics": None,
                "live": {}, "job": None, "pod": None, "pod_status": None, "started_at": None, "finished_at": None,
            }
            for m in MODELS
        }

    def log(self, msg, level="info"):
        self.events.append({"t": time.time(), "level": level, "msg": msg})

    def snapshot(self):
        return {
            "run_id": self.run_id, "state": self.state, "settings": self.settings, "started_at": self.started_at,
            "finished_at": self.finished_at, "now": time.time(), "db": self.db, "models": list(self.models.values()),
            "pods": self.pods, "events": list(self.events)[-80:], "namespace": NS,
        }


arena = Arena()


# ---------------------------------------------------------------- k8s helpers
def labels(role, **extra):
    return {"app.kubernetes.io/part-of": "embedding-arena", "arena/role": role, **extra}


def k8s_create_db():
    dep = client.V1Deployment(
        metadata=client.V1ObjectMeta(name="arena-postgres", labels=labels("db")),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(match_labels={"arena/role": "db"}),
            strategy=client.V1DeploymentStrategy(type="Recreate"),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels=labels("db")),
                spec=client.V1PodSpec(
                    termination_grace_period_seconds=5,
                    containers=[client.V1Container(
                        name="postgres", image=PG_IMAGE, image_pull_policy="IfNotPresent",
                        args=["-c", "max_connections=200", "-c", "shared_buffers=512MB", "-c", "maintenance_work_mem=256MB"],
                        env=[client.V1EnvVar(name=k, value=v) for k, v in
                             {"POSTGRES_USER": "arena", "POSTGRES_PASSWORD": "arena", "POSTGRES_DB": "arena", "PGDATA": "/data/pgdata"}.items()],
                        ports=[client.V1ContainerPort(container_port=5432)],
                        readiness_probe=client.V1Probe(
                            _exec=client.V1ExecAction(command=["pg_isready", "-U", "arena", "-d", "arena", "-h", "127.0.0.1"]),
                            period_seconds=2, initial_delay_seconds=2),
                        resources=client.V1ResourceRequirements(requests={"cpu": "500m", "memory": "512Mi"}, limits={"memory": "3Gi"}),
                        volume_mounts=[client.V1VolumeMount(name="data", mount_path="/data"),
                                       client.V1VolumeMount(name="shm", mount_path="/dev/shm")],
                    )],
                    volumes=[client.V1Volume(name="data", empty_dir=client.V1EmptyDirVolumeSource()),
                             client.V1Volume(name="shm", empty_dir=client.V1EmptyDirVolumeSource(medium="Memory", size_limit="1Gi"))],
                ),
            ),
        ),
    )
    svc = client.V1Service(
        metadata=client.V1ObjectMeta(name="arena-postgres", labels=labels("db")),
        spec=client.V1ServiceSpec(selector={"arena/role": "db"}, ports=[client.V1ServicePort(port=5432, target_port=5432)]),
    )
    for fn, obj in ((apps.create_namespaced_deployment, dep), (core.create_namespaced_service, svc)):
        try:
            fn(NS, obj)
        except ApiException as e:
            if e.status != 409:
                raise


def k8s_create_job(name, role, command, env, cpu_limit, mem_limit, extra_labels=None):
    lab = labels(role, **{"arena/run": arena.run_id, **(extra_labels or {})})
    job = client.V1Job(
        metadata=client.V1ObjectMeta(name=name, labels=lab),
        spec=client.V1JobSpec(
            backoff_limit=0, active_deadline_seconds=3600, ttl_seconds_after_finished=3600,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels=lab),
                spec=client.V1PodSpec(
                    restart_policy="Never", termination_grace_period_seconds=2,
                    containers=[client.V1Container(
                        name=role, image=IMAGE, image_pull_policy="IfNotPresent", command=command, working_dir="/app",
                        env=[client.V1EnvVar(name=k, value=str(v)) for k, v in {
                            "DATABASE_URL": DB_DSN, "REPORT_URL": REPORT_URL, "RUN_ID": arena.run_id,
                            "CACHE_DIR": "/cache", "HF_HOME": "/cache/hf", "JOB_NAME": name,
                            "PYTHONPATH": PYTHONPATH, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", **env}.items()],
                        resources=client.V1ResourceRequirements(
                            requests={"cpu": "500m", "memory": "512Mi"}, limits={"cpu": str(cpu_limit), "memory": mem_limit}),
                        volume_mounts=[client.V1VolumeMount(name="cache", mount_path="/cache"),
                                       client.V1VolumeMount(name="app", mount_path="/app", read_only=True)],
                    )],
                    volumes=[client.V1Volume(name="cache", host_path=client.V1HostPathVolumeSource(path=CACHE_HOST_PATH, type="DirectoryOrCreate")),
                             client.V1Volume(name="app", config_map=client.V1ConfigMapVolumeSource(name=APP_CONFIGMAP))],
                ),
            ),
        ),
    )
    batch.create_namespaced_job(NS, job)


def k8s_delete_jobs():
    sel = "arena/role in (worker,seeder)"
    batch.delete_collection_namespaced_job(NS, label_selector=sel, propagation_policy="Background")
    core.delete_collection_namespaced_pod(NS, label_selector=sel, grace_period_seconds=1)


def k8s_delete_db():
    for fn in (apps.delete_namespaced_deployment, core.delete_namespaced_service):
        try:
            fn("arena-postgres", NS)
        except ApiException as e:
            if e.status != 404:
                raise
    core.delete_collection_namespaced_pod(NS, label_selector="arena/role=db", grace_period_seconds=1)


def pod_view(p):
    status = p.status.phase
    reason = None
    for cs in p.status.container_statuses or []:
        if cs.state.waiting:
            reason = cs.state.waiting.reason
        elif cs.state.terminated:
            reason = cs.state.terminated.reason
        elif cs.state.running:
            reason = "Running" + ("" if cs.ready else " (not ready)")
    if p.metadata.deletion_timestamp:
        reason = "Terminating"
    lab = p.metadata.labels or {}
    return {
        "name": p.metadata.name, "role": lab.get("arena/role"), "model": lab.get("arena/model"), "run": lab.get("arena/run"),
        "phase": status, "reason": reason or status, "ip": p.status.pod_ip, "node": p.spec.node_name,
        "ready": any(c.ready for c in (p.status.container_statuses or [])),
        "created": p.metadata.creation_timestamp.timestamp() if p.metadata.creation_timestamp else None,
    }


def k8s_list_pods():
    pods = core.list_namespaced_pod(NS, label_selector="app.kubernetes.io/part-of=embedding-arena").items
    return [pod_view(p) for p in pods]


def k8s_job_states():
    out = {}
    for j in batch.list_namespaced_job(NS, label_selector="arena/role in (worker,seeder)").items:
        st = j.status
        out[j.metadata.name] = "failed" if st.failed else "succeeded" if st.succeeded else "active"
    return out


def db_stats():
    if arena._db_conn is None or arena._db_conn.closed:
        arena._db_conn = psycopg.connect(DB_DSN, application_name="arena-dashboard", autocommit=True, connect_timeout=3)
    with arena._db_conn.cursor() as cur:
        cur.execute(
            "SELECT application_name, state, count(*) FROM pg_stat_activity "
            "WHERE datname = 'arena' AND application_name LIKE 'arena-%' AND application_name <> 'arena-dashboard' GROUP BY 1, 2")
        conns = [{"app": a, "state": s, "count": c} for a, s, c in cur.fetchall()]
        cur.execute("SELECT pg_database_size('arena'), xact_commit, tup_inserted, tup_returned + tup_fetched FROM pg_stat_database WHERE datname = 'arena'")
        size, xact, ins, fetched = cur.fetchone()
        cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'emb\\_%'")
        tables = cur.fetchone()[0]
        cur.execute("SELECT coalesce(sum(n_live_tup), 0) FROM pg_stat_user_tables WHERE relname LIKE 'emb\\_%'")
        vectors = int(cur.fetchone()[0])
    now = time.time()
    stats = {"size_mb": round(size / 1e6, 1), "tables": tables, "vectors": vectors, "tps": 0, "inserts_ps": 0, "reads_ps": 0}
    if arena._prev_db:
        t, px, pi, pf = arena._prev_db
        dt = max(now - t, 1e-3)
        stats.update(tps=round((xact - px) / dt), inserts_ps=round((ins - pi) / dt), reads_ps=round((fetched - pf) / dt))
    arena._prev_db = (now, xact, ins, fetched)
    return conns, stats


# ---------------------------------------------------------------- control loop
async def launch_next():
    active = [m for m in arena.models.values() if m["status"] in ACTIVE]
    queued = [m for m in arena.models.values() if m["status"] in ("queued", "stopped")]
    while len(active) < arena.settings["max_parallel"] and queued:
        m = queued.pop(0)
        arena.attempt += 1
        name = f"bench-{m['slug']}-{arena.run_id}-{arena.attempt}"[:63].rstrip("-")
        m.update(status="scheduling", progress=0.0, message="job created, waiting for pod", job=name, pod=None,
                 started_at=time.time(), finished_at=None, live={}, metrics=None)
        threads = arena.settings["threads"]
        try:
            await asyncio.to_thread(k8s_create_job, name, "worker", ["python", "worker.py"],
                                    {"MODEL_ID": m["id"], "THREADS": threads, "OMP_NUM_THREADS": threads},
                                    threads, "4Gi", {"arena/model": m["slug"]})
            arena.log(f"job/{name} created for {m['name']}")
        except Exception as e:
            m.update(status="failed", message=f"could not create job: {e}")
            arena.log(f"failed to create job for {m['name']}: {e}", "error")
        active.append(m)


async def tick():
    try:
        arena.pods = await asyncio.to_thread(k8s_list_pods)
    except Exception as e:
        arena.log(f"k8s list pods failed: {e}", "error")
    for p in arena.pods:
        key = p["name"]
        if arena._pod_seen.get(key) != p["reason"]:
            arena._pod_seen[key] = p["reason"]
            arena.log(f"pod/{key} → {p['reason']}", "k8s")
    live_names = {p["name"] for p in arena.pods}
    for k in list(arena._pod_seen):
        if k not in live_names:
            arena._pod_seen.pop(k)
            arena.log(f"pod/{k} deleted", "k8s")

    db_pod = next((p for p in arena.pods if p["role"] == "db"), None)
    if arena.db["phase"] != "offline":
        if db_pod is None or not db_pod["ready"]:
            if arena.db["phase"] == "ready":
                arena.db["phase"] = "starting"
                arena.log("database pod is not ready", "warn")
        else:
            try:
                conns, stats = await asyncio.to_thread(db_stats)
                arena.db.update(connections=conns, stats=stats)
                if arena.db["phase"] != "ready":
                    arena.db["phase"] = "ready"
                    arena.log("pgvector database is ready and accepting connections", "success")
            except Exception as e:
                arena._db_conn = None
                if arena.db["phase"] == "ready":
                    arena.log(f"database stats unavailable: {e}", "warn")
        arena.db["pod"] = db_pod

    # attach pod info to models
    by_model = {}
    for p in arena.pods:
        if p["role"] == "worker" and p["run"] == arena.run_id:
            by_model[p["model"]] = p
    for m in arena.models.values():
        p = by_model.get(m["slug"])
        if p and m["status"] in ACTIVE:
            m["pod"], m["pod_status"] = p["name"], p["reason"]
            if m["status"] == "scheduling":
                m["message"] = f"pod {p['reason']}"

    async with arena.lock:
        if arena.state == "starting" and arena.db["phase"] == "ready":
            arena.state = "seeding"
            arena.db["seed"] = {"phase": "scheduling", "progress": 0, "message": "creating seed job"}
            arena.attempt += 1
            name = f"seed-{arena.run_id}-{arena.attempt}"
            await asyncio.to_thread(k8s_create_job, name, "seeder", ["python", "seed.py"],
                                    {"CORPUS_SIZE": arena.settings["corpus_size"]}, 2, "2Gi")
            arena.log(f"job/{name} created: loading BEIR SciFact into pgvector")

        if arena.state in ("seeding", "running"):
            try:
                jobs = await asyncio.to_thread(k8s_job_states)
            except Exception:
                jobs = {}
            for m in arena.models.values():
                if m["status"] in ACTIVE and jobs.get(m["job"]) == "failed":
                    m.update(status="failed", message=m["message"] or "job failed", finished_at=time.time())
                    arena.log(f"{m['name']} failed: {m['message']}", "error")
            seed = arena.db.get("seed") or {}
            if arena.state == "seeding" and seed.get("phase") != "completed" and any(
                    k.startswith("seed-") and v == "failed" for k, v in jobs.items()):
                arena.state = "error"
                arena.log("seeding failed — press RESET to try again", "error")

        if arena.state == "running":
            await launch_next()
            if not any(m["status"] in ACTIVE or m["status"] in ("queued", "stopped") for m in arena.models.values()):
                arena.state = "completed"
                arena.finished_at = time.time()
                arena.log("all models benchmarked — arena complete 🏁", "success")


async def loop():
    while True:
        try:
            await tick()
        except Exception as e:  # keep the control loop alive no matter what
            arena.log(f"control loop error: {e}", "error")
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(_app):
    try:
        await asyncio.to_thread(k8s_delete_jobs)
        await asyncio.to_thread(k8s_delete_db)
    except Exception as e:
        arena.log(f"startup cleanup: {e}", "warn")
    arena.log("dashboard online — press START to begin")
    task = asyncio.create_task(loop())
    yield
    task.cancel()


app = FastAPI(title="Embedding Arena", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/api/state")
async def state():
    return arena.snapshot()


@app.post("/api/start")
async def start():
    async with arena.lock:
        if arena.state in ("starting", "seeding", "running"):
            return {"ok": True, "state": arena.state}
        if arena.state == "completed":
            return JSONResponse({"ok": False, "error": "arena complete — press RESET to run again"}, 409)
        arena.started_at = arena.started_at or time.time()
        arena.finished_at = None
        seeded = (arena.db.get("seed") or {}).get("phase") == "completed"
        if arena.db["phase"] == "offline":
            arena.db["phase"] = "starting"
            await asyncio.to_thread(k8s_create_db)
            arena.log("deployment/arena-postgres created (pgvector on PostgreSQL 17)")
        if seeded:
            arena.state = "running"
            arena.log("resuming benchmarks")
        else:
            arena.state = "starting"
        for m in arena.models.values():
            if m["status"] == "stopped":
                m.update(status="queued", progress=0.0, message="")
    return {"ok": True, "state": arena.state}


@app.post("/api/stop")
async def stop():
    async with arena.lock:
        if arena.state not in ("starting", "seeding", "running"):
            return {"ok": True, "state": arena.state}
        arena.state = "stopped"
        for m in arena.models.values():
            if m["status"] in ACTIVE:
                m.update(status="stopped", message="stopped by user", progress=0.0, finished_at=time.time())
        if (arena.db.get("seed") or {}).get("phase") not in (None, "completed"):
            arena.db["seed"] = None
        await asyncio.to_thread(k8s_delete_jobs)
        arena.log("STOP — benchmark jobs deleted; database kept alive", "warn")
    return {"ok": True, "state": arena.state}


@app.post("/api/reset")
async def reset():
    async with arena.lock:
        await asyncio.to_thread(k8s_delete_jobs)
        await asyncio.to_thread(k8s_delete_db)
        arena._db_conn = None
        arena._prev_db = None
        arena.fresh()
        arena.log("RESET — jobs and database torn down", "warn")
    return {"ok": True, "state": arena.state}


@app.post("/api/settings")
async def settings(req: Request):
    body = await req.json()
    limits = {"max_parallel": (1, 8), "threads": (1, 8), "corpus_size": (200, 5000)}
    for k, (lo, hi) in limits.items():
        if k in body:
            arena.settings[k] = max(lo, min(hi, int(body[k])))
    arena.log(f"settings updated: parallel={arena.settings['max_parallel']} threads={arena.settings['threads']} corpus={arena.settings['corpus_size']}")
    return arena.settings


@app.post("/api/report")
async def report(req: Request):
    r = await req.json()
    if r.get("run_id") != arena.run_id or arena.state not in ("seeding", "running"):
        return {"ok": False, "ignored": True}
    phase = r.get("phase")
    if r.get("key") == "__seed__":
        arena.db["seed"] = {"phase": phase, "progress": r.get("progress", 0), "message": r.get("message", "")}
        if phase == "completed" and arena.state == "seeding":
            arena.db["dataset"] = r.get("dataset")
            arena.state = "running"
            arena.log(f"dataset loaded: {r.get('message')}", "success")
            arena.log(f"starting benchmarks — up to {arena.settings['max_parallel']} models in parallel")
        elif phase == "failed":
            arena.log(f"seed failed: {r.get('message')}", "error")
        return {"ok": True}
    m = arena.models.get(r.get("key"))
    if not m or m["status"] not in ACTIVE or r.get("job") != m["job"]:
        return {"ok": False, "ignored": True}
    if phase in PHASES:
        if m["status"] != phase:
            arena.log(f"{m['name']}: {phase} — {r.get('message', '')}")
        m.update(status=phase, progress=r.get("progress", m["progress"]), message=r.get("message", ""), live=r.get("live") or m["live"])
    elif phase == "completed":
        m.update(status="completed", progress=1.0, message=r.get("message", ""), metrics=r.get("metrics"), finished_at=time.time())
        done = sorted((x for x in arena.models.values() if x["status"] == "completed"), key=lambda x: -x["metrics"]["ndcg10"])
        rank = next(i for i, x in enumerate(done) if x is m) + 1
        arena.log(f"🏆 {m['name']} completed — nDCG@10 {m['metrics']['ndcg10']:.3f}, rank #{rank}", "success")
    elif phase == "failed":
        m.update(status="failed", message=r.get("message", ""), finished_at=time.time())
        arena.log(f"{m['name']} failed: {r.get('message')}", "error")
    return {"ok": True}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    try:
        while True:
            await sock.send_json(arena.snapshot())
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        pass
