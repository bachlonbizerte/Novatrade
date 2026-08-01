"""
Point d'entrée pour le VPS : tourne en continu, scanne toutes les 90 secondes.
Contrairement à scanner.main() (fait pour un run unique via GitHub Actions),
ce script ne s'arrête jamais — c'est systemd (nova-scanner.service) qui le
garde en vie et le relance automatiquement en cas de plantage ou de reboot.

Lancement : python -m src.run_forever
"""

import os
import time
import logging
from dotenv import load_dotenv

from src.exchange_client import ExchangeClient
from src.scanner import load_config, scan_once, scan_capital_once
from src.dashboard_publisher import push_dashboard_data
from src.capital_client import CapitalClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 90
DASHBOARD_PUSH_INTERVAL_SECONDS = 300


def run_forever():
    load_dotenv()
    logger.info("NOVA Scanner démarré en continu (VPS) — un scan toutes les 90s.")
    last_push = 0

    config = load_config()
    capital_client = None
    if config.get("capital", {}).get("enabled", False):
        capital_client = CapitalClient(
            api_key=os.getenv("CAPITAL_API_KEY", ""),
            identifier=os.getenv("CAPITAL_IDENTIFIER", ""),
            api_password=os.getenv("CAPITAL_API_PASSWORD", ""),
            demo=True,
        )

    while True:
        cycle_start = time.time()
        try:
            config = load_config()
            dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

            client = ExchangeClient(
                exchange_name=config["exchange"]["name"],
                api_key=os.getenv("EXCHANGE_API_KEY", ""),
                api_secret=os.getenv("EXCHANGE_API_SECRET", ""),
                dry_run=dry_run,
                fallback_exchanges=config["exchange"].get("fallback", []),
            )

            scan_once(config, client, bot_token, chat_id)

            if capital_client:
                try:
                    scan_capital_once(config, capital_client, bot_token, chat_id)
                except Exception as e:
                    logger.error(f"Erreur pendant le scan Capital.com (le reste continue): {e}")

            if time.time() - last_push >= DASHBOARD_PUSH_INTERVAL_SECONDS:
                push_dashboard_data()
                last_push = time.time()

        except Exception as e:
            logger.error(f"Erreur pendant le cycle de scan (on continue au prochain): {e}")

        elapsed = time.time() - cycle_start
        sleep_time = max(0, SCAN_INTERVAL_SECONDS - elapsed)
        logger.info(f"Cycle terminé en {elapsed:.1f}s — prochain scan dans {sleep_time:.0f}s")
        time.sleep(sleep_time)


if __name__ == "__main__":
    run_forever()
