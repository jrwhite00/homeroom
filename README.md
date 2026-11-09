# homeroom

## Talks as simulated nodes

Every session in the `sessions` index is a [KWOK](https://kwok.sigs.k8s.io/) simulated node on the kind cluster, named `talk-<session id>`. `python -m app.node_sync` (run from `backend/`) creates, updates and deletes the nodes so they match the index, and it is safe to run again: a second run with nothing changed reports `created 0, updated 0, deleted 0`.

- `homeroom.dev/slot` is the talk's start time in America/Denver, for example `tue-1430`.
- Each concept the talk assumes is a `prereq.homeroom.dev/<concept>:NoSchedule` taint. Talks that aren't tagged yet (or whose tags came back unknown) have no prerequisite taints and the label `homeroom.dev/tags=pending`. Run the sync again after tagging to add the taints.
- Real workloads stay off: every node has the `kwok.x-k8s.io/node=fake:NoSchedule` taint, and no `kubernetes.io/os` label, so DaemonSets don't target it.

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

## Simulated load and the chaos switch

Both exist only for the observability demo, and both are labeled as simulated wherever they show up.

**Simulated students (load generator).** `deploy/loadgen.yaml` is its own Deployment, scaled to 0. Start it with `kubectl -n homeroom scale deploy/loadgen --replicas=1` and stop it with `--replicas=0`. It plays five fictional, pseudonymous students sending a mix of searches (about 60%, some with everyday-language translation), week lookups (25%) and Kubernetes placement views (15%), about 2 requests a second (`LOADGEN_RPS`). No pseudonym is ever sent: every request carries `X-Homeroom-Simulated: loadgen`, and the backend marks those spans `simulated=true`.

**Chaos switch.** Adds a **simulated 800 ms delay to `/search`**. It is off by default.
- `GET /api/chaos` shows the state. While it's on, the app shows a yellow "Simulated slowdown is on" banner, and `/search` responses include an `induced_delay` note.
- Every span it affects (the `/search` server span and a `chaos.delay` child span) carries `induced=true`.
- Flipping it needs the token in the `homeroom-chaos` secret, created once by hand and never committed:

```
kubectl -n homeroom create secret generic homeroom-chaos --from-literal=token="$(openssl rand -hex 16)"
TOKEN=$(kubectl -n homeroom get secret homeroom-chaos -o jsonpath='{.data.token}' | base64 -d)
curl -X POST http://homeroom.localhost:18080/api/chaos -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"enabled": true}'
```
