# Interior voice sampling validation

Saving a speaker now exports up to five distributed identity fragments rather
than copying all their speech. Long exclusive interventions skip their first
three seconds and retain a 200 ms end margin. Six-second windows from separate
interventions are preferred; centered short speech is used only when insufficient
long speech is available. Full speaker audio downloads are unchanged.

Meeting matching uses the same interior selection. Saved WAV samples are already
selected audio and do not receive another three-second turn offset. The sampling
policy version invalidates old embedding caches. Match acceptance thresholds
remain unchanged. Detected speaker overlap is excluded; applause is not explicitly
classified, so an offset cannot guarantee noise-free speech.

## ITV check, 2026-10-01

Regenerated only Julie Etchingham's saved profile (ID 18), retaining its identity
and links. Original audio backed up locally before replacement. Selected five
six-second source windows beginning at 757.6, 1743.6, 4200.2, 6752.5 and 7770.6
seconds; saved sample duration is 30 seconds.

Matching all eight speakers against sixteen saved profiles:

| Run | Time | Correctly assigned |
|---|---:|---:|
| Recomputed embeddings under new policy | 20.57 s | 7/8 |
| Cached repeat | 0.208 s | 7/8 |

Julie is now assigned correctly. Nick Clegg is now unassigned, so the aggregate
recognition count has not improved. No wrong assignment was returned. The cold
run recomputed all 125 embeddings because the policy version changed; the warm
run computed none. These samples originate in the same recording and do not
establish recognition accuracy on other recordings. No meeting labels were
overwritten during this direct matching benchmark.

## Repeat after a new Nemotron 3 diarization

The subsequent user-requested rerun used the new 769-turn diarization of the
same meeting. All eight speakers matched their saved identities: 7.873 seconds
on the first matching pass and 0.098 seconds with cached embeddings. The earlier
7/8 result above remains recorded because the diarization input changed; these
are not directly comparable accuracy trials. Same-recording profiles still do
not establish performance on unseen recordings.
