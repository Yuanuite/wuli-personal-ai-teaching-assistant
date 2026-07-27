# Blueprint shape examples

## Multi-stage particle motion

- Targets: position after two stages; total electric work; unique release time.
- Physical stages: electric acceleration → magnetic half-turn → second electric stage → second magnetic half-turn.
- Reasoning steps: establish common time scale → derive stage update rule → accumulate position/velocity → apply receiver constraints → exclude other release intervals.
- Retrieval needs: half-period magnetic displacement; piecewise state update; uniqueness by interval exclusion.
- Obligations: every release interval considered; magnetic force does no work; capture time lies in an active electric interval.

## Dynamic circuit or induction

- Targets: induced EMF; terminal voltage; option verdicts.
- Physical stages: changing flux → equivalent EMF sources → branch current → measured terminal voltage.
- Reasoning steps: determine EMF direction and magnitude → construct equivalent circuit → apply node/loop relations → distinguish EMF from terminal voltage.
- Retrieval needs: Faraday-law sign; equivalent-source abstraction; terminal-voltage convention.
- Obligations: material sections and resistances mapped correctly; measurement endpoints fixed; every option receives a verdict.
