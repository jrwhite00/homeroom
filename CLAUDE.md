# Homeroom

A student picks a state and the courses they've taken. Homeroom sorts KubeCon talks into ready, one step away and not yet, and Kubernetes' own scheduler builds their week.
Tagline: "To learn the pod, you must become the pod."

## How it works
- curriculum/ holds concepts and courses, written by teachers. A course teaches concepts.
- AI tags each talk with the concepts it assumes (assumes and assumes_reason on the session).
- Readiness lives in backend/readiness.py. James chose the rule. Never edit it unless asked.
- Each talk is a KWOK simulated node. Prerequisites are taints named prereq.homeroom.dev/<concept>, seats are pod capacity, the time slot is a label.
- Each hour of a student's week is a pod. Its tolerations are the concepts the student knows. kube-scheduler decides where it goes. A cancelled talk gets a NoExecute taint and evicts its students.
- Kubernetes' pending message only says "untolerated taint(s)". We work out the missing concept by comparing taints with tolerations and say it in plain language.
- Each talk is also a point in concept space (concept_vec in Elasticsearch) for "easier than this talk" and "what next".

## Stack and layout
- backend/: Python FastAPI. frontend/: Next.js. Elasticsearch indexes: sessions, speakers, kiosks, standards, concepts, courses.
- curriculum/: concepts/*.yaml and courses/*.yaml. Teacher-owned.
- data/: standards, tags.json (cached AI tags), concept_dims.json.
- deploy/: Kubernetes manifests. Argo CD syncs this folder to the kind cluster.
- .gitar/rules/: review rules. Read them before changing curriculum/.
- Cluster: kind, context kind-cnh. KWOK v0.8.0 installed from ~/cnh-infra/kwok. Working examples in ~/cnh-infra/kwok/examples.

## Rules
- Check `kubectl config current-context` before any kubectl command. Use kind-cnh only. Never another context.
- Times are America/Denver.
- Student-facing text is plain language. No jargon without the decoder. Say "concept", never "topic".
- No real student data anywhere. Demo students are fictional with pseudonyms.
- Never use a student identifier as a metric label, span attribute or log field.
- No secrets in the repo, logs or output. Tokens come from Kubernetes secrets or environment variables. Never print their values.
- One concern per pull request, with tests. Keep diffs small.
- Never merge. After opening an eligible pull request, comment `gitar auto-apply:on` so Gitar can push fixes. Do not enable auto-apply on a pull request that changes `curriculum/`. Wait for Gitar's review and any fixes it pushes, then list its findings. James merges.
- `curriculum/` is teacher-owned: changes may only be authored by teachers in course pull requests. Gitar must not edit curriculum files or push curriculum fixes. A human must review the final curriculum diff before merge. Never edit a file there directly.
- Simulated things are labeled: seat counts, the load generator, the chaos switch (induced=true on affected spans).
- Report counts with _count, never _cat/indices (semantic chunks inflate docs.count).
- Don't add a dependency without saying why in the pull request.
- If a prompt conflicts with this file, say so before acting.
- Commit messages end with the Co-Authored-By line.

## Commands
Add the command here whenever you add one (run tests, start the app, sync nodes, load an index).

Commands below are Git Bash. Container paths need `MSYS_NO_PATHCONV=1` or Git Bash rewrites them.

- Cluster access: `export KUBECONFIG=~/.kube/kind-cnh` (context kind-cnh, built from the kind node's admin.conf and pointed at 127.0.0.1:36705). Recreate it with:
  `MSYS_NO_PATHCONV=1 docker exec cnh-control-plane cat /etc/kubernetes/admin.conf > ~/.kube/kind-cnh`, then `kubectl --kubeconfig ~/.kube/kind-cnh config set-cluster cnh --server=https://127.0.0.1:36705` and `config rename-context kubernetes-admin@cnh kind-cnh` (same --kubeconfig).
- Backend tests: `cd backend && pip install -r requirements-dev.txt && python -m pytest -q` (Python 3.12).
- Propose a course by hand (opens the same pull request a student-confirmed match does, `added by: James`): set `GITHUB_TOKEN` to a repository-scoped token (never a personal token; in the cluster it comes from the `homeroom-github` secret, key `token`), then run `cd backend && python -m app.propose_course --state Utah --course "Computer Programming 1" --standard "..." --teaches slug1,slug2 --reason "slug1=one line"`. The API is `POST /courses/propose`. Concepts must already exist on main. The API allows at most 5 open `course/*` pull requests, stores at most 20 local proposal files, and accepts 3 proposals a minute in total (behind the gateway every request has the same address). If GitHub fails, the concepts are checked against the `concepts` index and the file goes to `data/proposed-courses/`, never straight into curriculum/; if that index can't be read either, nothing is saved. Never print the token.
- Backend locally: `cd backend && uvicorn app.main:app --reload` (http://127.0.0.1:8000/healthz).
- Load the standards index: `cd backend && python -m app.load_standards --recreate` (about 3 minutes, ELSER embeds each description). First save the CSTA PDF from a browser as `data/standards/cache/csta-2026-pk12-cs-standards.pdf`; the Utah PDFs download themselves. Nothing in `data/standards/cache/` is ever committed. Needs `ES_API_KEY` (or `ES_LOCAL_API_KEY`) in the environment; `ES_URL` defaults to http://127.0.0.1:9200. Prints parsed vs `_count` per source and exits 1 on a mismatch. `--dry-run` parses only; `--pdf-dir` reads PDFs from another folder.
- Frontend locally: `cd frontend && npm ci && npm run dev` (http://127.0.0.1:3000).
- Simulated students (load generator): `kubectl -n homeroom scale deploy/loadgen --replicas=1` to start, `--replicas=0` to stop. Chaos switch (simulated 800 ms on /search, off by default): token in secret `homeroom-chaos` (create once: `kubectl -n homeroom create secret generic homeroom-chaos --from-literal=token="$(openssl rand -hex 16)"`), then `POST /api/chaos {"enabled": true|false}` with `Authorization: Bearer <token>`. Never print the token. See README.md.
- Dash0 dashboard and targets live in observability/dash0/ (dashboard.json, two SLO files). Push them: `cd backend && DASH0_API_URL=https://api.us-west-2.aws.dash0.com DASH0_API_TOKEN=<token> python -m app.dash0_sync push`; after editing the dashboard in Dash0, `... export` writes it back. Browser monitoring reads DASH0_WEB_URL and DASH0_WEB_TOKEN from the `homeroom-dash0-web` secret (keys url, token). Never print tokens.
- Build images: `docker build -t homeroom-backend:0.1.0 backend && docker build -t homeroom-frontend:0.1.0 frontend`
- Compare search modes on the real sessions index: `cd backend && python -m app.search_demo "autoscaling load balancing high availability"` (hybrid vs keyword side by side; needs `ES_API_KEY` or `ES_LOCAL_API_KEY`, `ES_URL` defaults to http://127.0.0.1:9200). The API is `GET /search?q=...&mode=hybrid|keyword` (`/api/search` through the gateway).
- Load the curriculum: `cd backend && python -m app.load_curriculum --recreate` (concepts and courses indexes; refuses if a course teaches a concept with no file). Try a practice table without touching the real indexes: `--dir <folder> --index-prefix practice-`, then `python -m app.search_demo "<question>" --translate --concepts-index practice-concepts`. `/search?translate=true` turns the question into the closest concepts' technical words first.
- Tag sessions with the concepts they assume: `cd backend && python -m app.tag_sessions` (shows what would be sent, sends nothing), then `--yes` to send the untagged talks as one Message Batches request (`claude-haiku-5-5` by default, `--model` to change; key from `ANTHROPIC_API_KEY`). Results are cached in `data/tags.json`, so a rerun with nothing changed costs nothing; an interrupted run resumes its batch from `data/.tags-batch.json`. `--apply-only` rewrites cached tags onto the index after a reload; `--retry-unknown` resends unknown ones.
- Sync talk nodes (one KWOK node per session): `cd backend && KUBECONFIG="$(cygpath -w ~/.kube/kind-cnh)" python -m app.node_sync` (`--dry-run` to only report). Creates, updates and deletes `talk-<id>` nodes to match the sessions index; a rerun with nothing changed reports 0 changes. Run it again after tagging to add the prereq taints. It refuses any context but kind-cnh. Needs KWOK, installed with `wsl -d Ubuntu -- cat /home/eslingen/cnh-infra/kwok/kwok.yaml | kubectl --kubeconfig "$(cygpath -w ~/.kube/kind-cnh)" --context kind-cnh apply -f -`, then the same for `stage-fast.yaml` (`MSYS_NO_PATHCONV=1`). Seat counts are simulated; see README.md.
- Load images into kind (no kind CLI here): `for n in cnh-control-plane cnh-worker; do docker save homeroom-backend:0.1.0 homeroom-frontend:0.1.0 | MSYS_NO_PATHCONV=1 docker exec -i $n ctr -n k8s.io images import -; done`, then `kubectl -n homeroom rollout restart deploy` if the tag didn't change.
- Register the Argo CD app (once): `kubectl apply -f argocd/application.yaml`. It syncs deploy/ from main.
- Place students with kube-scheduler (`cd backend`, KUBECONFIG set, ES key in env): `python -m app.placer place --class demo --student <pseudonym> --slot thu-1430 --knows a,b`, `learn --class demo --student <pseudonym> --concept containers` (adds the toleration; Pending pods get retried), `watch --class demo [--once]` (controller loop: phases and FailedScheduling messages into the placements index for `GET /placements?class=`, recreates evicted pods), `cancel <node>` / `uncancel <node>` (NoExecute), `test-node create|delete` (simulated node in slot test-0000 with a containers prereq). Pseudonyms only, never real names.
- Open the app through the gateway: `kubectl -n envoy-gateway-system port-forward svc/$(kubectl -n envoy-gateway-system get svc -l gateway.envoyproxy.io/owning-gateway-name=demo -o name | cut -d/ -f2) 18080:80`, then http://homeroom.localhost:18080 (backend at /api/healthz).
