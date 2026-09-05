# CureMyBill 🩺

Application Streamlit qui analyse une facture médicale américaine (PDF ou image),
extrait les codes CPT via Claude Vision, compare les montants à un barème de
référence, et génère une lettre de contestation en anglais.

## Installation

```bash
python -m venv venv
source venv/bin/activate   # Windows : venv\Scripts\activate
pip install -r requirements.txt
```

## Lancement

```bash
streamlit run app.py
```

L'app s'ouvre dans le navigateur (en général http://localhost:8501).

## Clé API

Tu as besoin d'une clé API Anthropic (https://console.anthropic.com/).
Elle se saisit directement dans la barre latérale de l'app — elle n'est jamais
écrite sur le disque, elle reste seulement en mémoire pendant la session.

## Barème de référence (`fee_schedule.csv`)

Le fichier contient ~150 codes CPT/HCPCS courants (urgences, imagerie, labos,
injections, codes J, petites procédures), avec le **tarif national officiel
Medicare** pour chacun — pas un "prix juste" inventé.

**Pourquoi Medicare et pas un "prix moyen du marché" ?** Parce que Medicare
publie ses tarifs, ils sont vérifiables, et c'est un argument factuel et
citable dans une lettre ("vous facturez 15x le tarif Medicare") plutôt qu'une
opinion contestable sur ce qui serait "juste". C'est l'approche utilisée par
les vrais services d'aide aux patients (Dollar For, etc.).

**Important** : un hôpital facture *normalement* bien plus que Medicare (souvent
5 à 30x selon les études citées par CMS/OIG). Ce n'est donc pas en soi une
preuve de fraude. L'app classe les écarts en 3 niveaux :
- **Typical range** — jusqu'à ~5x le tarif Medicare (marge hospitalière habituelle)
- **Above typical markup** — 5x à 10x (à surveiller)
- **Well above typical — worth disputing** — plus de 10x (c'est cette catégorie
  qui alimente la lettre de contestation)

Les seuils (5x / 10x) sont réglables directement dans `compare_to_schedule()`
dans `app.py` si tu veux les ajuster selon ton retour terrain.

Pour aller plus loin, tu peux enrichir ou remplacer ce fichier avec :
- Le CMS Physician Fee Schedule complet (gratuit, mais nécessite un script
  d'import à cause des ajustements géographiques par région — demande-moi si
  tu veux ce script)
- Fair Health Consumer (https://www.fairhealthconsumer.org/) pour des tarifs
  "usuels et coutumiers" par région
- Healthcare Bluebook

Format attendu :

```csv
cpt_code,description,standard_price
99213,Office visit established patient (level 3),92
```

Tu peux aussi brancher une vraie base de données ou une API externe à la place
du CSV en modifiant la fonction `load_fee_schedule()` dans `app.py`.

## Notes techniques

- Le modèle utilisé est `claude-sonnet-5` (vision + texte).
- L'extraction demande à Claude de répondre en JSON strict, ce qui rend le
  parsing fiable.
- Les PDF sont envoyés directement à l'API (support natif des documents),
  les images en PNG/JPEG.
- La lettre de contestation est téléchargeable directement en PDF (via
  `reportlab`), avec mise en page lettre professionnelle et espace réservé
  pour une signature manuscrite.
- Aucune donnée n'est persistée : tout reste en mémoire de session Streamlit.

## Limites à garder en tête

- Le barème inclus est un exemple, pas une source médicale ou légale fiable.
- L'extraction par Claude Vision peut faire des erreurs sur des scans de
  mauvaise qualité — vérifie toujours les codes CPT extraits avant d'envoyer
  une lettre.
- Ce n'est pas un conseil juridique : pour des montants importants, une
  consultation avec un "patient advocate" ou un avocat spécialisé reste
  recommandée.
