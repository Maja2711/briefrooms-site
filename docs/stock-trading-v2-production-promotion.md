# Stock Trading v2 production promotion

Stock Trading v1 remains the production Champion until Stock Trading v2 proves a prospective out-of-sample advantage under the formal V1-vs-V2 promotion bridge.

The intended transition is capability-gated, not a manual release decision: v2 must accumulate immutable prospective shadow decisions, be evaluated on samples that were not used to construct the challenger, pass fixed-N and fresh disjoint holdout gates, and only then may production routing switch from the v1 Champion to the v2 engine. Hard safety invariants remain non-learnable and cannot be weakened by the bridge.

The bridge must also monitor post-promotion live evidence. If promoted v2 performance violates the rollback trigger, routing returns automatically to the immutable v1 parent Champion and the failed v2 transition is blocked until a new evidence epoch is created.
