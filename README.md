# Search Term Optimizer

Analyse les termes de recherche d'un compte Google Ads et propose, pour chacun :

- de **l'acheter en mot clé** (terme pertinent dont la performance est prouvée) ;
- de **l'exclure** (mauvaise performance, ou sémantique hors sujet) ;
- de **ne rien faire**.

L'outil combine des règles de performance que tu règles par client et une analyse sémantique par Gemini, que tu entraînes en validant ou corrigeant ses propositions. Il **ne modifie jamais le compte** : il produit des fichiers CSV à importer dans Google Ads Editor.

> **Crédit.** Ce projet s'inspire très largement de [Negative Keyword Cleaner](https://github.com/google-marketing-solutions/negative_keyword_cleaner), publié par Google (google-marketing-solutions) sous licence Apache 2.0. Le principe de « l'étudiant IA », le contexte annonceur, les exports Editor et le déploiement Cloud Run viennent de là. Voir [NOTICE](NOTICE) et la section Crédits du guide.

📖 **Le fonctionnement détaillé et la FAQ sont dans [docs/GUIDE.md](docs/GUIDE.md).**

## Ce qu'il faut avant de commencer

| Élément | Où l'obtenir |
|---|---|
| Developer token Google Ads | Centre API du compte administrateur (MCC) |
| Identifiant du MCC | Google Ads, sans tirets |
| Client OAuth « Application Web » | Google Cloud > API et services > Identifiants |
| Clé API Gemini | Google AI Studio |
| Python 3.11 ou plus | Testé avec Python 3.13 |

Sans developer token, l'outil ne peut lire aucun compte. Le mode démo (ci-dessous) permet de voir l'interface en attendant.

## Essayer sans aucune clé (mode démo)

```bash
pip install -r requirements.txt
DEMO_MODE=1 streamlit run src/app.py
```

Ouvre http://localhost:8080. Le compte est fictif et l'IA est simulée par des règles simples : cela montre le parcours, pas la qualité réelle des recommandations.

## Lancer en local sur un vrai compte

1. Dans Google Cloud, active l'API Google Ads et crée un client OAuth de type **Application Web** avec `http://localhost:8080` comme URI de redirection autorisé.
2. Copie `.env.example` en `.env` et remplis-le.
3. Lance :

```bash
pip install -r requirements.txt
streamlit run src/app.py
```

4. Connecte-toi avec le compte Google qui a accès au MCC.

## Déployer sur Cloud Run

Il faut les droits pour créer un service Cloud Run, un dépôt Artifact Registry et un bucket dans un projet Google Cloud, ainsi que `gcloud`, `docker` et `terraform` installés.

```bash
# 1. Dépôt d'images (une seule fois)
gcloud artifacts repositories create sto --repository-format=docker --location=europe-west1

# 2. Construire et pousser l'image
gcloud auth configure-docker europe-west1-docker.pkg.dev
docker build -t europe-west1-docker.pkg.dev/MON_PROJET/sto/sto:v1 .
docker push europe-west1-docker.pkg.dev/MON_PROJET/sto/sto:v1

# 3. Infrastructure
cd terraform
cp secrets.tfvars.example secrets.tfvars   # puis remplis-le
terraform init -upgrade
terraform apply -var-file secrets.tfvars
```

4. Terraform affiche `app_url`. Ajoute cette URL aux **URI de redirection autorisés** du client OAuth.

À savoir :

- Comme l'outil d'origine, l'URL est publique, mais rien n'est accessible sans une connexion Google donnant accès au MCC. Pour restreindre davantage, remplace `allUsers` dans `terraform/main.tf`.
- La mémoire par client est stockée dans un bucket versionné, que `terraform destroy` ne supprime pas tant qu'il contient des fichiers.
- Ne définis jamais `GOOGLE_ADS_REFRESH_TOKEN` sur Cloud Run. L'application l'ignore de toute façon dans cet environnement.
- Pour changer une variable après le premier déploiement : `gcloud run services update search-term-optimizer --update-env-vars=NOM=valeur --region=europe-west1`.
- Pour une nouvelle version du code : pousse une image `:v2`, puis `gcloud run deploy search-term-optimizer --image=...:v2 --region=europe-west1`.

## Configuration

| Variable | Obligatoire | Rôle |
|---|---|---|
| `GOOGLE_ADS_API_TOKEN` | oui | Developer token |
| `MCC_ID` | oui | MCC utilisé pour lister les comptes |
| `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET` | oui | Client OAuth |
| `OAUTH_REDIRECT_URI` | oui | URL de l'application (`http://localhost:8080` en local) |
| `GOOGLE_API_KEY` | oui | Clé API Gemini |
| `GEMINI_MODEL` | non | Modèle Gemini (défaut : `gemini-3.1-flash-lite`, comme l'outil d'origine) |
| `DEFAULT_BUCKET_NAME` | non | Bucket GCS de la mémoire par client (sinon `data/clients/`) |
| `GOOGLE_ADS_REFRESH_TOKEN` | non | Local uniquement, évite la reconnexion |
| `BATCH_SIZE` | non | Taille des lots d'entraînement (défaut : 15) |
| `DEMO_MODE` | non | `1` pour le compte fictif |

## Structure du code

```
src/app.py            Interface Streamlit (les 4 étapes)
src/core/rules.py     Règles de performance, marques, garde-fous (sans IA)
src/core/llm.py       Prompts et appels Gemini
src/core/scoring.py   Enchaînement règles -> IA -> garde-fous
src/core/ads.py       Requêtes Google Ads (lecture seule)
src/core/export.py    Tableaux de résultats et CSV Google Ads Editor
src/core/storage.py   Mémoire par client (fichier local ou GCS)
src/core/auth.py      Connexion Google (OAuth)
src/core/demo.py      Compte fictif et IA simulée
terraform/            Déploiement Cloud Run
tests/                Tests (pytest)
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Les tests couvrent les règles, les garde-fous, les exports et le parcours complet de l'interface en mode démo. Ils n'appellent ni Google Ads ni Gemini.

## État de validation

Le cœur métier et l'interface sont testés sur des données fictives. **Les appels réels à l'API Google Ads et à Gemini n'ont pas encore été exécutés** : ils demandent un developer token et une clé. Prévois une première session de vérification sur un petit compte, en particulier pour les termes Performance Max (ressource `campaign_search_term_view`) et pour l'import des CSV dans Google Ads Editor.

## Licence et avertissement

Licence Apache 2.0 (voir [LICENSE](LICENSE)). Outil fourni tel quel, sans garantie. Il n'est ni affilié à Google ni approuvé par Google. Chaque recommandation doit être relue par un humain avant d'être appliquée.
