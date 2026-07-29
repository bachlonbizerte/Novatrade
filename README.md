# Trading Bot

Bot de trading crypto simple (stratégie de croisement de moyennes mobiles),
conçu pour tourner gratuitement via **GitHub Actions** (cron), avec migration
facile vers un VPS plus tard.

## ⚠️ Avertissement

Ceci est un projet de démarrage à but éducatif. Le trading automatisé comporte
des risques de perte financière. Teste **toujours** en mode `DRY_RUN=true`
(ou en environnement testnet de l'exchange) avant d'utiliser de l'argent réel.
Ceci n'est pas un conseil financier.

## 🎯 Gestion du risque par trade

Chaque position ouverte (via le bouton "✅ ACHETER") utilise :
- **Stop-loss fixe : 1%** sous le prix d'entrée
- **Take-profit dynamique : entre 2% et 5%**, calculé automatiquement selon la
  volatilité réelle du marché à l'instant T (ATR%) — un marché agité vise un
  objectif plus large, un marché calme un objectif plus proche et plus vite atteignable

## 🧾 Historique complet des actions (mémoire du bot)

Chaque clic sur un bouton (Acheter / Ignorer / Rescan), **y compris les
échecs** (ex: erreur API, ordre refusé), est journalisé dans
`docs/data/action_log.json` via `action_log.py`. En parallèle,
`paper_trading.py` calcule un taux de réussite **par crypto** à partir des
positions déjà clôturées.

Le moteur de décision (`ai_decision.py`) utilise cet historique : si une
crypto a un mauvais taux de réussite sur ses derniers trades (< 40% sur au
moins 3 trades), son score est réduit par prudence ; si son taux est bon
(> 70%), le score est renforcé. C'est la mécanique d'apprentissage à partir
des performances passées — pas un modèle qui "apprend" au sens machine
learning, mais un ajustement factuel basé sur ce qui s'est vraiment passé.

## 🤖 Second avis IA (Claude, optionnel)

En plus du scoring par règles, le bot peut demander un avis qualitatif à
Claude (Anthropic) sur chaque signal envoyé — un regard en langage naturel
qui peut souligner des nuances qu'un score seul ne capture pas.

Pour l'activer :
1. `pip install anthropic` (déjà dans `requirements.txt`)
2. Ajoute `ANTHROPIC_API_KEY` à ton `.env` (local) ou aux secrets GitHub Actions
3. Sans cette clé, le bot fonctionne exactement pareil — cette étape est simplement ignorée

⚠️ C'est une couche d'aide à la décision supplémentaire, pas un remplacement
du scoring ni une garantie de justesse.

## Structure

```
trading-bot/
├── src/
│   ├── strategy.py           # logique de la stratégie (SMA crossover)
│   ├── analyzers.py          # analyse technique + agrégation multi-timeframe
│   ├── tradingview_module.py # signal externe TradingView (consensus tiers)
│   ├── ai_decision.py        # moteur de décision: combine tout en 1 score final
│   ├── paper_trading.py      # positions simulées suivies (SL/TP auto + stats par crypto)
│   ├── action_log.py         # journal de toutes les actions, succès ET échecs
│   ├── notification_limiter.py # plafond quotidien de notifications
│   ├── ai_agent.py            # second avis qualitatif via Claude (optionnel)
│   ├── exchange_client.py    # wrapper ccxt (données + ordres)
│   ├── backtester.py         # moteur de backtest
│   ├── run_backtest.py       # script CLI pour lancer un backtest
│   ├── telegram_notifier.py  # envoi de notifs Telegram avec boutons
│   ├── telegram_listener.py  # écoute les clics sur les boutons (process continu)
│   ├── scanner.py            # scanne les 10 cryptos + notifie + sauvegarde pour le dashboard
│   └── main.py                # bot mono-paire legacy (1 check = 1 exécution)
├── config/
│   └── config.example.yaml
├── docs/
│   ├── index.html            # dashboard web (à héberger via GitHub Pages)
│   └── data/latest_scan.json # généré automatiquement par le scanner à chaque run
├── tests/
├── .github/workflows/
│   └── trading-bot.yml       # lance le scanner toutes les 15 min + commit les résultats
├── requirements.txt
└── .env.example
```

## 🎯 Sélectivité stricte des notifications

Par défaut le bot est réglé pour être **très sélectif**, pas bavard :
- Seuil strict : `notify_score_threshold: 90` — en dessous, rien n'est envoyé, même si c'est la "meilleure" crypto du scan
- **Un seul signal par run** : parmi les cryptos qui dépassent le seuil, seule celle avec le score le plus haut est notifiée (pas une notif par crypto qualifiée)
- Plafond quotidien : `max_notifications_per_day: 15`, remis à zéro chaque jour (minuit UTC), pour éviter le spam même si le marché est agité

⚠️ Un score élevé reflète la qualité *technique* de la configuration de marché selon nos critères (tendance, momentum, volume, structure, risque) — **ce n'est pas une probabilité de gain**. Aucun système ne peut garantir un taux de réussite donné ; c'est justement pour ça que le paper trading existe (voir plus bas).

## 📈 Paper trading (suivi de performance)

Quand tu cliques "✅ Acheter" sur une notification (en mode `DRY_RUN=true`), le
bot n'exécute pas juste un ordre simulé "tiré et oublié" — il ouvre une
**position suivie** avec stop-loss et take-profit (définis dans
`config.yaml → risk`). À chaque run du scanner, ces positions sont vérifiées :
si le prix atteint le SL ou le TP, la position se clôture automatiquement et
tu reçois une notification.

Toutes ces données sont stockées dans `docs/data/trades.json` et agrégées en
statistiques (taux de réussite, PnL cumulé, nombre de trades) visibles sur le
dashboard. C'est la base pour juger objectivement si la stratégie est bonne
**avant** de passer en argent réel.

## 🧠 Comment fonctionne le score final

`ai_decision.py` combine 3 sources pour chaque crypto :
1. **Analyse technique multi-timeframe** (15m / 1h / 4h) — RSI, MACD, tendance EMA, volume, volatilité, pondérés par timeframe
2. **Signal TradingView** — consensus de dizaines d'indicateurs tiers (via `tradingview-ta`, non officiel)
3. **Bonus/malus de cohérence** — si les deux sources s'accordent, le score est renforcé ; si elles se contredisent, il est réduit (signal d'incertitude)

⚠️ C'est un **moteur de règles pondérées**, pas un modèle de machine learning entraîné sur des données. C'est volontairement transparent — le raisonnement de chaque décision est inclus dans la notification Telegram et sur le dashboard (clique sur une carte pour le voir).

## 📊 Dashboard web (gratuit, via GitHub Pages)

1. Sur GitHub : **Settings → Pages → Source: Deploy from a branch → Branch: main, dossier `/docs`**
2. Ton dashboard sera accessible à `https://TON_USER.github.io/trading-bot/`
3. Il se met à jour automatiquement à chaque run du scanner (le workflow commit `docs/data/latest_scan.json`)

## ⚙️ Deux processus distincts, deux hébergements différents

| Processus | Rôle | Où le déployer |
|---|---|---|
| `src/scanner.py` | Analyse les 10 cryptos, envoie les notifs Telegram avec score + boutons | **GitHub Actions** (cron, gratuit) — s'exécute puis s'arrête |
| `src/telegram_listener.py` | Capte tes clics "Acheter / Attendre / Passer" et déclenche l'action | Doit tourner **en continu** → PAS GitHub Actions. Utilise un worker gratuit (Railway/Render) ou ta future VM/VPS |

Sans le listener actif, les boutons s'afficheront dans Telegram mais un clic
ne déclenchera rien — c'est normal, il faut avoir déployé le listener quelque
part pour qu'il "écoute".

## Créer ton bot Telegram

1. Ouvre Telegram, cherche **@BotFather**, envoie `/newbot` et suis les étapes
2. Récupère le token (format `123456789:ABC-DEF...`) → `TELEGRAM_BOT_TOKEN`
3. Envoie n'importe quel message à ton nouveau bot
4. Va sur `https://api.telegram.org/bot<TON_TOKEN>/getUpdates` dans ton navigateur
5. Repère `"chat":{"id": 123456789}` → c'est ton `TELEGRAM_CHAT_ID`

## Installation locale

```bash
git clone <ton-repo>
cd trading-bot
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
cp config/config.example.yaml config/config.yaml
# édite .env avec tes clés API et config/config.yaml avec tes paramètres
```

## Lancer un backtest

```bash
python -m src.run_backtest --csv data/BTCUSDT_15m.csv
```

## Lancer le bot une fois (localement)

```bash
python -m src.main            # bot mono-paire legacy
python -m src.scanner         # scan des 10 cryptos + notif Telegram
python -m src.telegram_listener   # écoute les boutons (à laisser tourner)
```

## Déploiement gratuit via GitHub Actions

1. Pousse ce repo sur GitHub.
2. Dans **Settings → Secrets and variables → Actions**, ajoute :
   - `EXCHANGE_API_KEY`
   - `EXCHANGE_API_SECRET`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. Le workflow `.github/workflows/trading-bot.yml` s'exécute automatiquement
   toutes les 15 minutes (modifiable via l'expression cron).
4. Tu peux aussi le lancer manuellement depuis l'onglet **Actions** du repo
   (bouton "Run workflow").

⚠️ GitHub Actions n'est pas fait pour du temps réel à la milliseconde — c'est
suffisant pour une stratégie qui vérifie le marché toutes les X minutes, mais
si tu as besoin de réactivité instantanée, migre vers un VPS.

## Prochaine étape : passer sur VPS

Quand tu es prêt, tu pourras déployer `src/main.py` en boucle continue
(avec un `while True` + `time.sleep`) sur un VPS, ou utiliser un
orchestrateur comme `systemd`/`supervisor`/Docker pour le garder actif.

## Tests

```bash
pip install pytest
pytest tests/
```
