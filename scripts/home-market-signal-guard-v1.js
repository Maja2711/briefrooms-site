/* BriefRooms emergency no-op.
 * The previous DOM MutationObserver guard was removed because it could
 * cause main-thread churn on the homepage. Signal semantics are enforced
 * by scripts/home-market-signal-v6.js and server-side/CI validation.
 */
(function () {
  'use strict';
})();