# Homeroom

A student picks a state and the courses they've taken. Homeroom sorts KubeCon talks into ready, one step away and not yet, and Kubernetes' own scheduler builds their week.
Tagline: "To learn the pod, you must become the pod."

## How it works
- curriculum/ holds concepts and courses, written by teachers. A course teaches concepts.
- AI tags each talk with the concepts it assumes (assumes and assumes_reason on the session).
- Readiness lives in backend/readiness.py, written by James: 0 missing concepts = ready, 1 = one step away, 2 or more = not yet, no tags = unknown. Never edit it unless asked.
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
- Never merge. Open the pull request, wait for Gitar's review, list its findings, and James decides. Never tick an "Apply fix" box.
- curriculum/ changes only through reviewed course pull requests. Never edit a file there directly.
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
- Backend locally: `cd backend && uvicorn app.main:app --reload` (http://127.0.0.1:8000/healthz).
- Load the standards index: `cd backend && python -m app.load_standards --recreate` (about 3 minutes, ELSER embeds each description). First save the CSTA PDF from a browser as `data/standards/cache/csta-2026-pk12-cs-standards.pdf`; the Utah PDFs download themselves. Nothing in `data/standards/cache/` is ever committed. Needs `ES_API_KEY` (or `ES_LOCAL_API_KEY`) in the environment; `ES_URL` defaults to http://localhost:9200. Prints parsed vs `_count` per source and exits 1 on a mismatch. `--dry-run` parses only; `--pdf-dir` reads PDFs from another folder.
- Frontend locally: `cd frontend && npm ci && npm run dev` (http://127.0.0.1:3000).
- Build images: `docker build -t homeroom-backend:0.1.0 backend && docker build -t homeroom-frontend:0.1.0 frontend`
- Compare search modes on the real sessions index: `cd backend && python -m app.search_demo "autoscaling load balancing high availability"` (hybrid vs keyword side by side; needs `ES_API_KEY` or `ES_LOCAL_API_KEY`, `ES_URL` defaults to http://localhost:9200). The API is `GET /search?q=...&mode=hybrid|keyword` (`/api/search` through the gateway).
- Load the curriculum: `cd backend && python -m app.load_curriculum --recreate` (concepts and courses indexes; refuses if a course teaches a concept with no file). Try a practice table without touching the real indexes: `--dir <folder> --index-prefix practice-`, then `python -m app.search_demo "<question>" --translate --concepts-index practice-concepts`. `/search?translate=true` turns the question into the closest concepts' technical words first.
- Tag sessions with the concepts they assume: `cd backend && python -m app.tag_sessions` (shows what would be sent, sends nothing), then `--yes` to send the untagged talks as one Message Batches request (`claude-haiku-5-5` by default, `--model` to change; key from `ANTHROPIC_API_KEY`). Results are cached in `data/tags.json`, so a rerun with nothing changed costs nothing; an interrupted run resumes its batch from `data/.tags-batch.json`. `--apply-only` rewrites cached tags onto the index after a reload; `--retry-unknown` resends unknown ones.
- Load images into kind (no kind CLI here): `for n in cnh-control-plane cnh-worker; do docker save homeroom-backend:0.1.0 homeroom-frontend:0.1.0 | MSYS_NO_PATHCONV=1 docker exec -i $n ctr -n k8s.io images import -; done`, then `kubectl -n homeroom rollout restart deploy` if the tag didn't change.
- Register the Argo CD app (once): `kubectl apply -f argocd/application.yaml`. It syncs deploy/ from main.
- Place students with kube-scheduler (`cd backend`, KUBECONFIG set, ES key in env): `python -m app.placer place --class demo --student <pseudonym> --slot thu-1430 --knows a,b`, `learn --class demo --student <pseudonym> --concept containers` (adds the toleration; Pending pods get retried), `watch --class demo [--once]` (controller loop: phases and FailedScheduling messages into the placements index for `GET /placements?class=`, recreates evicted pods), `cancel <node>` / `uncancel <node>` (NoExecute), `test-node create|delete` (simulated node in slot test-0000 with a containers prereq). Pseudonyms only, never real names.
- Open the app through the gateway: `kubectl -n envoy-gateway-system port-forward svc/$(kubectl -n envoy-gateway-system get svc -l gateway.envoyproxy.io/owning-gateway-name=demo -o name | cut -d/ -f2) 18080:80`, then http://homeroom.localhost:18080 (backend at /api/healthz).
