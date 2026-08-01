"""
Point d'entrée pour le VPS : tourne en continu, scanne toutes les 60 secondes.
Contrairement à scanner.main() (fait pour un run unique via GitHub Actions),
ce script ne s'arrête jamais — c'est systemd (nova-scanner.service) qui le
garde en vie et le relance automatiquement en cas de plantage ou de reboot.

Le traitement des boutons Telegram (Acheter/Ignorer/Rescan/Clôturer/Prolonger)
est géré séparément par telegram_listener.py, en parallèle, pour une réaction
instantanée plutôt qu'un délai d'attente jusqu'au prochain scan.

Lancement : python -m src.run_forever
"""

import os
import time
import logging
from dotenv import load_dotenv

from src.exchange_client import ExchangeClient
from src.scanner import load_config, scan_once
from src.dashboard_publisher import push_dashboard_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 90
DASHBOARD_PUSH_INTERVAL_SECONDS = 300  # toutes les ~5 min, pas à chaque cycle (éviter de spammer les commits)


def run_forever():
    load_dotenv()
    logger.info("NOVA Scanner démarré en continu (VPS) — un scan toutes les 60s.")
    last_push = 0

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
