import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class BackgroundPushContractTest(unittest.TestCase):
    def test_frontend_uses_push_manager(self):
        text=(ROOT/"scripts/trading-notifications.js").read_text(encoding="utf-8")
        self.assertIn("pushManager.subscribe", text)
        self.assertIn("trading-push-subscribe", text)
        self.assertIn("trading-push-unsubscribe", text)

    def test_service_worker_handles_push_and_click(self):
        text=(ROOT/"br-trading-sw.js").read_text(encoding="utf-8")
        self.assertIn('addEventListener("push"', text)
        self.assertIn('addEventListener("notificationclick"', text)
        self.assertIn("delivery_id", text)

    def test_backend_contract_exists(self):
        for rel in [
            "supabase/functions/trading-push-subscribe/index.ts",
            "supabase/functions/trading-push-unsubscribe/index.ts",
            "supabase/functions/trading-push-dispatch/index.ts",
            "supabase/functions/trading-push-click/index.ts",
            "supabase/functions/trading-push-analytics/index.ts",
            "supabase/migrations/20261001_trading_push.sql",
        ]:
            self.assertTrue((ROOT/rel).is_file(), rel)

    def test_background_push_disabled_until_endpoint_configured(self):
        cfg=json.loads((ROOT/"data/notifications/trading-notification-config.json").read_text(encoding="utf-8"))
        self.assertFalse(cfg["background_push"]["enabled"])
        self.assertIsNone(cfg["background_push"]["api_base"])

if __name__=="__main__":
    unittest.main()
