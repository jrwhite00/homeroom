# Homeroom

> **To learn the pod, you must become the pod.**

A high school student picks their state and the computer science courses they've taken. Homeroom sorts every KubeCon talk into **ready**, **one step away** and **not yet**, and then Kubernetes' own scheduler builds their week.

- **Talks are nodes.** Each talk is a simulated Kubernetes node. The concepts a talk assumes are taints on that node.
- **Students are pods.** Each hour of a student's week is a pod, and the concepts they know are its tolerations. kube-scheduler decides where each hour goes.
- **Pending tells the student what to learn.** When an hour can't be placed, Kubernetes only says "untolerated taint(s)". Homeroom compares the taints with the tolerations and tells the student, in plain words, which concept they're missing. For a talk that's one step away, it suggests a beginner talk or a project booth that covers that concept.

## Architecture

- **Backend:** Python FastAPI (`backend/`). Endpoints:
  - `/search`: hybrid search, with optional everyday-language translation.
  - `/week`: the student's talks sorted by readiness.
  - `/placements`: where Kubernetes put the student's hours.
  - `/courses/propose`: a teacher proposes a course.
  - `/chaos`: the chaos switch.
- **Frontend:** Next.js (`frontend/`), behind Envoy Gateway at `homeroom.localhost`.
- **Readiness rule:** `backend/readiness.py`, written by James. 0 missing concepts = ready, 1 = one step away, 2 or more = not yet. A talk with no tags is unknown, never ready.
- **AI tagging:** Claude (Message Batches, `claude-haiku-5-5`) reads each talk's abstract and picks which concepts from the teachers' concept table it assumes, with a reason for each. The results are cached in `data/tags.json`.
- **Cluster:** kind with KWOK v0.8.0, where each of the 600 sessions is a `talk-<id>` node. Argo CD syncs `deploy/` from `main`.
- **Elasticsearch** (9.5.6, local) holds every index:
  - **Talk data:** `sessions`, `speakers`, `kiosks`
  - **Curriculum:** `standards`, `concepts`, `courses`
  - **Placements:** `placements`, where student hours end up

  How the search and translation work:
  - **Embeddings:** `semantic_text` fields use ELSER embeddings.
  - **`/search`:** fuses BM25 and semantic results with an `rrf` retriever.
  - **Translation:** semantic search over the concept definitions turns "how do big websites stay up when everyone visits" into the technical words talks actually use.
- **Gitar** reviews every pull request.
  - It follows the rules in `.gitar/rules/`.
  - On course pull requests, the curriculum check posts a comment a teacher can read.
  - With `gitar auto-apply:on` it pushes its own fixes. In the demo PR #33, which deliberately broke `readiness()`, Gitar found the cause of the CI failure and pushed the fix.
- **Dash0** is where we watch the app, and where the teacher's view lives: a bar chart on the dashboard showing which concepts most often keep a class "one step away" from talks. It answers the teacher's question: "What should I teach before the trip?" Under the hood:
  - The Dash0 operator auto-instruments the backend.
  - The backend adds its own spans and metrics.
  - The dashboard and two targets (SLOs) are stored as code in `observability/dash0/`.
  - The student page has browser monitoring.
  - Flipping the chaos switch shows up on the dashboard within about two minutes.
- **Radar** is the organizer's view. Look there to see how many students are having trouble finding sessions, and what would happen if a talk got cancelled. It runs in the `radar` namespace, and its MCP server lets the coding agent read the cluster too.

## Setup from a clean laptop

You need Docker, [kind](https://kind.sigs.k8s.io/), kubectl, Helm, Python 3.12 and Node 22. Every command below is run from the repo root unless it says otherwise.

### 1. Cluster

```
kind create cluster --name cnh --image kindest/node:v1.37.0 --config - <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes: [{role: control-plane}, {role: worker}]
EOF
```

This creates the kubectl context `kind-cnh`. The cluster tools refuse to run against any other context.

Install the platform with Helm:
- **Argo CD:** chart `argo/argo-cd`, namespace `argocd`.
- **Envoy Gateway:** `oci://docker.io/envoyproxy/gateway-helm` v1.10.0, namespace `envoy-gateway-system`. Then create a GatewayClass `eg` and a Gateway named `demo` in namespace `demo`, listening on HTTP port 80.
- **Kyverno:** chart `kyverno/kyverno`. It enforces the cluster policy `require-requests-limits`.
- **metrics-server:** chart `metrics-server/metrics-server`, with `--kubelet-insecure-tls` on kind.
- **Dash0 operator:** chart `dash0-operator/dash0-operator`, namespace `dash0-system`.
  - Its Dash0 auth token goes in the secret `dash0-authorization-secret`, key `token`.
  - Turn on Python auto-instrumentation: `DASH0_ENABLE_PYTHON_AUTO_INSTRUMENTATION=true`.
- **Radar:** namespace `radar`.
- **KWOK v0.8.0:** apply its `kwok.yaml` and `stage-fast.yaml` from the [KWOK release](https://github.com/kubernetes-sigs/kwok/releases/tag/v0.8.0).

### 2. Elasticsearch

```
curl -fsSL https://elastic.co/start-local | sh -s -- -v 9.5.6
```

This writes `elastic-start-local/.env`. Export its API key as `ES_API_KEY` (the backend also reads `ES_LOCAL_API_KEY`), and set `ES_URL` if Elasticsearch isn't at `http://localhost:9200`. ELSER (`.elser-2-elasticsearch`) downloads itself the first time it's used. That can take a few minutes.

The `sessions`, `speakers` and `kiosks` indexes come from the public schedule (see [Data sources](#data-sources-and-licenses)). They were loaded before the hackathon started, and **that loader is not in this repo.** The fields the app reads are:
- **sessions:** `id`, `title`, `abstract`, `track`, `level`, `is_talk`, `starts_at`, `room` and `speaker_names`, plus a `semantic` (ELSER) field.
- **kiosks:** `project`, `kiosk`, `starts_at`, `ends_at` and `room_short`.

Then, from `backend/` (`pip install -r requirements-dev.txt` first):

```
python -m app.load_curriculum --recreate      # concepts and courses from curriculum/
python -m app.load_standards --recreate       # see data/standards/README.md for the one manual download
python -m app.tag_sessions                    # shows what would be sent; add --yes to tag (needs ANTHROPIC_API_KEY)
```

### 3. Deploy

```
docker build -t homeroom-backend:0.1.0 backend && docker build -t homeroom-frontend:0.1.0 frontend
kind load docker-image homeroom-backend:0.1.0 homeroom-frontend:0.1.0 --name cnh
kubectl apply -f argocd/application.yaml      # once; Argo CD then syncs deploy/ from main
cd backend && python -m app.node_sync         # one KWOK node per session; run again after tagging
```

Open the app with:

```
kubectl -n envoy-gateway-system port-forward svc/$(kubectl -n envoy-gateway-system get svc -l gateway.envoyproxy.io/owning-gateway-name=demo -o name | cut -d/ -f2) 18080:80
```

Then go to http://homeroom.localhost:18080. The backend is under `/api/`.

`CLAUDE.md` lists every other command: tests, placing students, cancelling a talk and pushing the Dash0 dashboard.

### Secrets and environment variables

Values never go in the repo. Create each secret by hand.

| Secret (namespace) | Keys | Used for | Without it |
| --- | --- | --- | --- |
| `homeroom-chaos` (homeroom) | `token` | Flipping the chaos switch | The switch can't be flipped |
| `homeroom-github` (homeroom) | `token` | Course proposals open pull requests | Proposals are saved to `data/proposed-courses/` |
| `homeroom-dash0-web` (homeroom) | `url`, `token` | Browser monitoring, using an ingest-only token | The page sends nothing |
| `dash0-authorization-secret` (dash0-system) | `token` | The Dash0 operator | No telemetry |

| Environment variable | Used by |
| --- | --- |
| `ES_URL`, `ES_API_KEY` (or `ES_LOCAL_API_KEY`) | The backend and every loader |
| `ANTHROPIC_API_KEY` | `app.tag_sessions` |
| `DASH0_API_URL`, `DASH0_API_TOKEN` | `app.dash0_sync` (dashboard and SLOs) |
| `KUBECONFIG` | `app.node_sync` and `app.placer`, which refuse any context except `kind-cnh` |
| `LOADGEN_RPS` | The load generator, about 2 requests a second by default |

### Tests

```
cd backend && python -m pytest -q
```

CI runs the same command on every pull request.

## Talks as simulated nodes

Every session in the `sessions` index is a [KWOK](https://kwok.sigs.k8s.io/) simulated node on the kind cluster, named `talk-<session id>`. `python -m app.node_sync` (run from `backend/`) creates, updates and deletes the nodes so they match the index. It is safe to run again: a second run with nothing changed reports `created 0, updated 0, deleted 0`.

- `homeroom.dev/slot` is the talk's start time in America/Denver, for example `tue-1430`.
- Each concept the talk assumes is a `prereq.homeroom.dev/<concept>:NoSchedule` taint. Talks that aren't tagged yet, or whose tags came back unknown, have no prerequisite taints and carry the label `homeroom.dev/tags=pending`. Run the sync again after tagging to add the taints.
- Real workloads stay off these nodes. Every node has the `kwok.x-k8s.io/node=fake:NoSchedule` taint and no `kubernetes.io/os` label, so DaemonSets don't target it.
- A cancelled talk gets the `homeroom.dev/cancelled:NoExecute` taint, which evicts its students. The placement controller then recreates their hours so the scheduler can find somewhere else for them.

### Seat counts are simulated

The KubeCon schedule doesn't publish room capacities, so each node's pod capacity is **a made-up number based on the room's size**. Every node carries the label `homeroom.dev/seats=simulated`.

| Room | Seats |
| --- | ---: |
| Keynote hall (Halls A-C, Halls 1-5) | 5000 |
| Combined Grand Ballrooms (ACE, BDF, GI), Hall DE | 1000 |
| Regency Ballroom B-D | 600 |
| Single Grand Ballroom (H, J) | 500 |
| Regency Ballroom CD, Salt Lake Ballroom DE | 400 |
| Combined meeting rooms (250 A-C, 251 D-F, 255 BC, ...) | 300 |
| Regency Ballroom A or B, Salt Lake Ballroom C | 250 |
| Single meeting rooms (151, 254, 255 A, 255 D) | 150 |
| Foyers, lobbies, registration, off-site, other rooms | 100 |
| Solutions Showcase stages and Project Pavilion | 80 |

The rules are `SEAT_RULES` in `backend/app/node_sync.py`.

## Induced load and the chaos switch

Both exist only for the observability demo. **Both are simulated, and they're labeled as induced wherever they show up**, so nobody mistakes them for real students or a real outage.

**Simulated students (load generator).** `deploy/loadgen.yaml` is its own Deployment, scaled to 0.
- Start it with `kubectl -n homeroom scale deploy/loadgen --replicas=1` and stop it with `--replicas=0`.
- It plays five fictional, pseudonymous students sending a mix of requests:
  - searches, about 60%, some using everyday-language translation;
  - week lookups, 25%;
  - views of where Kubernetes placed them, 15%.
- No pseudonym is ever sent. Every request carries `X-Homeroom-Simulated: loadgen`, and the backend marks those spans and metrics `simulated=true`.

**Chaos switch.** It adds an **induced 800 ms delay to `/search`** and is off by default.
- `GET /api/chaos` shows its state.
- While it's on, the app shows a yellow "Simulated slowdown is on" banner, and `/search` responses include an `induced_delay` note.
- Every span it affects carries `induced=true`: the `/search` server span and a `chaos.delay` child span. The request metrics carry `induced` too.
- Flipping it needs the token from the `homeroom-chaos` secret:

```
kubectl -n homeroom create secret generic homeroom-chaos --from-literal=token="$(openssl rand -hex 16)"
TOKEN=$(kubectl -n homeroom get secret homeroom-chaos -o jsonpath='{.data.token}' | base64 -d)
curl -X POST http://homeroom.localhost:18080/api/chaos -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"enabled": true}'
```

## Observability (Dash0)

On top of the operator's auto-instrumentation, the backend adds its own spans and metrics. **No span, metric or log carries a student identifier.**

**Spans:**
- `homeroom.search`: the mode, whether translation was used, the concepts it translated to, and the result count.
- `homeroom.week`: group counts.
- `homeroom.scheduling`: counts of placed and pending student hours.

**Metrics:**
- `homeroom.http.requests` and `homeroom.http.duration`, broken down by route, status class, `simulated` and `induced`.
- `homeroom.students.pending`.
- `homeroom.student_days`, split by whether the student got at least three talks that day.
- `homeroom_missing_concept`, by `concept`, `class_code` and `simulated`: one count per student and concept when their week leaves them one step away from a talk because of that concept.

**Teacher's view:** on the dashboard, pick your class in the Class dropdown. The "top missing concepts" bar chart shows which concepts kept your students one step away from talks over the last hour. The tallest bar is the best thing to teach before the trip. When the load generator is running, its simulated students are counted too.

**Targets** (Dash0 SLOs, rolling 4 weeks):
- 99% of schedule requests (`/week`) finish in under 2 seconds.
- 99% of students are placed in at least three talks a day.

The dashboard and both targets live in `observability/dash0/`.
- `python -m app.dash0_sync push` uploads them.
- `python -m app.dash0_sync export` writes the live dashboard back to the repo.

Browser monitoring (`@dash0/sdk-web`) reads its endpoint and ingest-only token from the `homeroom-dash0-web` secret at runtime.

## Organizer's view (Radar)

Every hour of a student's week is a pod, so a student who can't get into any talk that hour shows up in Radar as a pod that couldn't be scheduled.

- **Who's stuck:** open the scheduling issues for a class's namespace (`class-<name>`) to see how many students are having trouble finding sessions right now, and in which hours.
- **What if we cancel this talk?** Open the talk's node (`talk-<session id>`) and look at its drain plan. It lists the students who'd be bumped. Nothing changes until someone runs `cd backend && python -m app.placer cancel talk-<session id>`.

Radar shows students by pod name, which is a pseudonym plus the time slot. Its messages are Kubernetes' own ("untolerated taint"), so they don't say which concept is missing. Homeroom works that out and tells the student in plain words.

## Teacher-owned curriculum

- **Concepts:** Anna, a computer science teacher, writes them. Each one is a file in `curriculum/concepts/` with three fields:
  - `concept`: the plain name;
  - `definition`: one sentence a student can understand;
  - `technical words`: the words talks actually use.
- **Courses:** each course in `curriculum/courses/` lists the concepts it `teaches` and the state standard it comes from.
- **Every change to `curriculum/` goes through a pull request.** `.github/CODEOWNERS` makes Anna the required reviewer, so no course or concept lands without her approval.
- **Gitar's curriculum check:** on course pull requests, it checks four things:
  - every concept the course teaches has a file;
  - the course teaches at most four concepts;
  - a standard is named;
  - there are no personal details.

  It explains any problem in plain words, and it never approves or pushes changes to course files.
- **Teachers' wording is used as written.** The app shows Anna's definitions exactly as she wrote them. The AI only picks which of her concepts a talk assumes. It never writes a definition.

## Data sources and licenses

- **KubeCon + CloudNativeCon North America 2026 schedule** (Salt Lake City, November 9-12, 2026). It covers the sessions, speakers and Project Pavilion kiosks. The data comes from the public event schedule published by the CNCF and the Linux Foundation, and it stays with them and the speakers. It's loaded into Elasticsearch and isn't committed to this repo.
- **CSTA K-12 computer science standards.** Computer Science Teachers Association (2026), *2026 CSTA PK-12 Computer Science Standards*, https://csteachers.org/pk12standards/. Licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
  - Homeroom uses them non-commercially.
  - The standards text is never stored in this repo. It's downloaded and indexed at load time.
  - Anything we derive from it is shared under the same license.
- **Utah standards.** Utah State Board of Education: the Utah 6-12 Computer Science Standards and the CTE strands and standards documents. They're also downloaded at load time and never committed.
- Sources, hashes and counts are in `data/standards/README.md`.
- **Students:** there are no real students anywhere. Every demo student is fictional, with a pseudonym.

## What existed before 11:00

The hackathon build window was 11:00-17:30 America/Denver on November 9, 2026. Everything below existed before 11:00. Everything in this repo after the initial empty commit was built during the window.

- **The kind cluster `cnh`**, created November 8, with one control-plane node and one worker. It already had:
  - Argo CD
  - Kyverno, with the `require-requests-limits` policy
  - Envoy Gateway, with the `demo` Gateway
  - metrics-server and local-path storage
  - the Dash0 operator
  - Radar
- **Demo workloads** in the `demo` namespace: `podinfo`, `configwatch` and a `podinfo-probe` CronJob. The `manifest` namespace held a service bridging the cluster to the laptop's Elasticsearch.
- **Elasticsearch 9.5.6** via start-local, with ELSER deployed and the public schedule loaded: 600 sessions, 712 speakers and 216 kiosks.
- **The accounts:** GitHub, Gitar, Dash0, Radar and Anthropic. Container base images were pulled ahead of time.
- **The config drafts:**
  - a draft of `CLAUDE.md`
  - KWOK v0.8.0 manifests and examples, applied after 11:00
  - a CI workflow draft
  - a practice repository Anna used to rehearse writing concepts. Nothing from it was copied here, and `curriculum/` started empty.
- **This repository:** created at 10:10 with only a one-line README.

## AI use
Claude code was used to help write most PRs, Standards and course mappings were hand made with Gitar reviewing them for changes that needs to be made to DB tables. For design decisions A.I had n only a conversational role, all design was done with the purposeful intent of our team (humans).
