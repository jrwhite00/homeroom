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
