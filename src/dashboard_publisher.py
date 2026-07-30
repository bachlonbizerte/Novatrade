"""
Le scanner tourne maintenant sur le VPS et écrit ses données localement
(docs/data/*.json). Sans GitHub Actions pour faire le `git push` comme avant,
c'est ce module qui s'en charge, pour que le dashboard GitHub Pages reste
à jour. Silencieux si rien n'a changé ou si git échoue (ne doit jamais
interrompre le scan).
"""

import subprocess
import logging

logger = logging.getLogger(__name__)


def push_dashboard_data(repo_path: str = "/root/Novatrade"):
    try:
        subprocess.run(["git", "add", "docs/data/"], cwd=repo_path, check=True,
                        capture_output=True, timeout=15)

        diff = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=repo_path,
                               capture_output=True, timeout=15)
        if diff.returncode == 0:
            return  # rien de nouveau à publier

        subprocess.run(["git", "commit", "-m", "chore: mise à jour des données (VPS) [skip ci]"],
                        cwd=repo_path, check=True, capture_output=True, timeout=15)
        subprocess.run(["git", "push"], cwd=repo_path, check=True, capture_output=True, timeout=30)
        logger.info("Données du dashboard publiées sur GitHub.")

    except subprocess.CalledProcessError as e:
        logger.warning(f"Échec de la publication des données (le scan continue): "
                        f"{e.stderr.decode() if e.stderr else e}")
    except Exception as e:
        logger.warning(f"Erreur lors de la publication des données (le scan continue): {e}")
