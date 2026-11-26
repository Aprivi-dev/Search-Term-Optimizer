Guide de Search Term Optimizer

Ce document explique à quoi sert l'outil, comment il fonctionne en détail et répond aux questions les plus probables. Pour l'installation et le déploiement, voir le [README](../README.md).

## Sommaire

1. [À quoi sert l'outil](#1-à-quoi-sert-loutil)
2. [Crédits : ce que l'on doit à Negative Keyword Cleaner](#2-crédits--ce-que-lon-doit-à-negative-keyword-cleaner)
3. [Vue d'ensemble du fonctionnement](#3-vue-densemble-du-fonctionnement)
4. [Le parcours, étape par étape](#4-le-parcours-étape-par-étape)
5. [Les règles de performance](#5-les-règles-de-performance)
6. [Ce que fait l'IA, et ce qu'elle ne fait pas](#6-ce-que-fait-lia-et-ce-quelle-ne-fait-pas)
7. [Les garde-fous](#7-les-garde-fous)
8. [La mémoire par client](#8-la-mémoire-par-client)
9. [Les fichiers produits](#9-les-fichiers-produits)
10. [Limites connues](#10-limites-connues)
11. [FAQ](#11-faq)

---

## 1. À quoi sert l'outil

Le rapport des termes de recherche est la source d'optimisation la plus directe d'un compte Google Ads, et aussi la plus longue à dépouiller. Sur un gros compte, il contient des milliers de lignes par mois.

Search Term Optimizer fait ce tri à ta place et te laisse la décision. Pour chaque terme de recherche, il rend l'une de ces trois recommandations :

| Recommandation | Quand | Ce que tu obtiens |
|---|---|---|
| **À acheter** | Le terme correspond à l'offre du client et sa performance est prouvée | Un mot clé à ajouter, avec le groupe d'annonces où le ranger |
| **À exclure** | Le terme coûte sans rapporter, ou il est hors sujet | Un mot clé à exclure, avec son type de correspondance et son niveau |
| **À ignorer** | Pas assez de données, ou rien à faire | Rien, mais la raison est notée |

Il couvre les campagnes **Search**, **DSA** (annonces dynamiques), **Shopping** et **Performance Max**.

Trois principes guident l'outil :

- **Lecture seule.** Il ne modifie jamais le compte. Il produit des fichiers CSV que tu importes toi-même dans Google Ads Editor, après relecture.
- **Les chiffres sont jugés par des règles, le sens par l'IA.** Tu fixes les seuils de performance de chaque client. L'IA ne recalcule pas la performance : elle juge la pertinence du terme et rédige la justification.
- **Un client, une mémoire.** Contexte, seuils, marques et corrections sont enregistrés par compte et ne se mélangent jamais.

## 2. Crédits : ce que l'on doit à Negative Keyword Cleaner

Cet outil est une adaptation assumée de **[Negative Keyword Cleaner](https://github.com/google-marketing-solutions/negative_keyword_cleaner)**, publié par Google dans l'organisation *google-marketing-solutions* sous licence Apache 2.0. Merci à ses auteurs : sans leur travail, celui-ci n'existerait pas sous cette forme.

L'outil d'origine relit les mots clés **déjà exclus** d'un compte et repère ceux qui bloquent du trafic utile. Search Term Optimizer applique la même méthode à un autre objet : les **termes de recherche**, pour décider lesquels acheter et lesquels exclure.

Ce qui est repris de l'original :

- le concept de **« l'étudiant IA »** : l'IA propose, l'humain valide ou corrige par petits lots, et les corrections servent d'exemples pour les lots suivants ;
- le **contexte annonceur** construit en résumant le site du client, avec un « prompt positif » (ce qu'il vend) et un « prompt négatif » (ce qu'on ne veut pas cibler) ;
- la **décision accompagnée d'une raison et d'un score de priorité** de 1 à 5, obtenue par sortie structurée de Gemini ;
- l'**indicateur d'accord** entre l'IA et l'humain, lot après lot ;
- le traitement du reste du compte par **paquets de 50** une fois l'entraînement jugé suffisant ;
- le choix de **ne jamais écrire dans le compte** et d'exporter au format Google Ads Editor ;
- la pile technique (Streamlit, Gemini, OAuth Google, Cloud Run) et la trame du déploiement Terraform.

Ce qui est nouveau :

| | Negative Keyword Cleaner | Search Term Optimizer |
|---|---|---|
| Objet analysé | Mots clés à exclure existants | Termes de recherche |
| Décisions | Garder / Retirer | Acheter / Exclure / Ignorer |
| Données de performance | Aucune | Impressions, clics, coût, conversions, valeur |
| Règles chiffrées | Aucune | Seuils CPA ou ROAS réglables par client |
| Marques | Décrites dans le prompt négatif | Listes par client, avec une consigne par liste |
| Placement des mots clés | Sans objet | L'IA choisit le groupe d'annonces |
| Mémoire | Perdue à la fin de la session | Enregistrée par client |
| Types de campagnes | Mots clés à exclure du compte | Search, DSA, Shopping, Performance Max |

Le code a été réécrit pour ce nouvel usage. Negative Keyword Cleaner n'est pas un produit officiellement supporté par Google, et Search Term Optimizer n'est ni affilié à Google ni approuvé par Google.

## 3. Vue d'ensemble du fonctionnement

```
 Compte Google Ads (lecture seule)
        │  termes de recherche + métriques, mots clés, groupes d'annonces, exclusions
        ▼
 ┌─────────────────────────────┐
 │ 1. Nettoyage                │  retire les termes déjà exclus
 ├─────────────────────────────┤
 │ 2. Règles de performance    │  GOOD / BAD / NEUTRAL selon TES seuils     (sans IA)
 ├─────────────────────────────┤
 │ 3. Règles de marques        │  concurrents, marque du client             (sans IA)
 ├─────────────────────────────┤
 │ 4. Analyse sémantique       │  Gemini : acheter / exclure / ignorer,
 │                             │  raison, priorité, groupe cible, texte à exclure
 ├─────────────────────────────┤
 │ 5. Garde-fous               │  rend chaque proposition applicable        (sans IA)
 └─────────────────────────────┘
        ▼
 Toi : D'accord / Corriger  ──►  mémoire du client  ──►  réinjectée à l'étape 4
        ▼
 Fichiers CSV pour Google Ads Editor
```

Les étapes 1, 2, 3 et 5 sont du code déterministe : mêmes données, mêmes seuils, même résultat. Seule l'étape 4 fait appel à l'IA.

## 4. Le parcours, étape par étape

### Connexion et choix du client

Tu te connectes avec ton compte Google. L'outil liste les comptes actifs du MCC et tu en choisis un dans la barre latérale. Changer de client remet l'analyse à zéro et charge la mémoire de ce client.

### Étape 1 : contexte client

À remplir une fois par client, puis à ajuster quand tu veux.

- **Site du client.** Le bouton « Analyser le site » lit la page d'accueil et Gemini en tire un résumé à puces. Relis-le et corrige-le : c'est la base de tout le jugement sémantique.
- **Ce qu'on ne veut pas cibler.** Par exemple : particuliers, occasion, emploi, SAV, gammes non vendues.
- **Objectif et seuils.** CPA ou ROAS, valeur cible, et les seuils décrits au chapitre 5.
- **Marques.** Une liste de marques concurrentes et une liste pour la marque du client, chacune avec sa consigne : exclure systématiquement, traiter comme un terme normal, ou ignorer.

Rien ne se passe tant que le contexte n'est pas enregistré. Si tu modifies les seuils ou les marques après avoir chargé des termes, l'analyse en cours repart de zéro avec les nouvelles règles ; les décisions déjà mémorisées pour le client sont conservées.

### Étape 2 : termes de recherche

Tu choisis :

- la **période** (par défaut les 30 derniers jours, hors aujourd'hui) ;
- les **types de campagnes** ;
- un **minimum d'impressions** par terme, pour écarter la longue traîne sans signal ;
- un **nombre maximum de termes**, gardés par coût décroissant.

L'outil affiche ensuite le nombre de termes, le coût total et la répartition entre bonne performance, mauvaise performance et données insuffisantes.

### Étape 3 : entraînement

L'outil tire un lot de 15 termes : la moitié parmi les plus coûteux, l'autre au hasard pour varier les cas. L'IA rend ses propositions, affichées en trois colonnes (à acheter, à exclure, à ignorer).

Chaque carte montre le terme, sa campagne, ses métriques, le signal de performance avec son explication, la proposition concrète et la raison donnée par l'IA. Tu as deux boutons :

- **D'accord** : la proposition est retenue et mémorisée comme bon exemple.
- **Corriger** : tu choisis la décision, le groupe cible ou le texte à exclure, le type de correspondance, le niveau, et tu écris pourquoi. Cette raison est ce qui apprend le plus à l'IA.

En haut de page, l'indicateur « Accord IA / toi » montre la part des propositions du lot que tu as validées sans changer la décision. Quand il est durablement élevé, l'entraînement a fait son travail.

Trois suites possibles :

- **Nouveau lot** pour continuer à entraîner ;
- **Analyser les termes restants** : l'IA traite tout le reste par paquets de 50, avec tes exemples ;
- **Arrêter et voir les résultats** : seuls les termes que tu as traités sont exportés.

### Étape 4 : résultats

Quatre onglets, chacun avec son tableau et son téléchargement : mots clés à acheter, groupes d'annonces à créer, exclusions, analyse complète. Le chapitre 9 décrit ces fichiers. Tu peux reprendre l'entraînement à tout moment.

## 5. Les règles de performance

Chaque terme reçoit un signal calculé uniquement à partir de ses métriques et des seuils du client. Les valeurs entre parenthèses sont les réglages par défaut.

**Sans aucune conversion :**

- **BAD** si le terme a au moins X clics (25) ;
- **BAD** si le terme a coûté au moins X € (60) ;
- **NEUTRAL** sinon.

**Avec des conversions, en mode CPA :**

- **BAD** si le CPA dépasse la cible multipliée par le ratio « mauvais » (2), et que la dépense atteint le seuil de coût ci-dessus ;
- **GOOD** si le terme a au moins X conversions (2) et un CPA inférieur ou égal à la cible multipliée par la tolérance (1) ;
- **NEUTRAL** sinon.

**Avec des conversions, en mode ROAS** (valeur de conversion divisée par le coût) :

- **BAD** si le ROAS est inférieur à la cible divisée par le ratio « mauvais », et que la dépense atteint le seuil de coût ;
- **GOOD** si le terme a au moins X conversions et un ROAS supérieur ou égal à la cible divisée par la tolérance ;
- **NEUTRAL** sinon.

Exemple en mode CPA, cible 30 € : un terme à 80 clics, 64 € et 6 conversions a un CPA de 10,67 €, donc GOOD. Un terme à 140 clics, 150 € et 1 conversion a un CPA de 150 €, supérieur à 60 €, donc BAD. Un terme à 14 clics, 11 € et 1 conversion reste NEUTRAL : une seule conversion ne prouve rien.

Conseils de réglage :

- Fixe le seuil de coût sans conversion autour de 2 fois le CPA cible.
- Sur un compte à faible volume, allonge la période plutôt que de baisser les seuils.
- Une tolérance de 1,2 accepte à l'achat des termes jusqu'à 20 % moins bons que la cible.

Les conversions utilisées sont celles de la colonne « Conversions » de Google Ads (actions de conversion principales), pas « Toutes les conversions ».

## 6. Ce que fait l'IA, et ce qu'elle ne fait pas

Gemini reçoit, pour chaque paquet de termes :

- le contexte du client (résumé de l'offre, ce qu'on ne veut pas cibler) ;
- tes 60 dernières décisions validées ou corrigées pour ce client ;
- pour chaque terme : son texte, sa campagne, son groupe d'annonces, son type, ses métriques et son signal de performance ;
- pour les campagnes où un achat est possible : la liste des groupes d'annonces avec quelques mots clés de chacun.

Ses consignes, en résumé :

1. **Acheter** seulement si le signal est GOOD, que la campagne est Search ou DSA, et que le terme correspond à l'offre. Choisir le groupe d'annonces existant le plus proche, ou répondre « NOUVEAU » si aucun ne convient.
2. **Exclure** un terme hors sujet, même avec peu de données.
3. **Exclure** un terme au signal BAD, sauf s'il est au cœur de l'offre : dans ce cas l'ignorer et recommander de travailler l'enchère, l'annonce ou la page.
4. **Ne jamais exclure un terme qui convertit bien.** S'il paraît hors sujet, le laisser en « ignorer » avec une priorité élevée pour que tu tranches.
5. Dans le doute, ignorer.

Pour une exclusion, l'IA choisit aussi :

- **le texte et le type de correspondance** : le mot fautif seul en expression quand c'est lui qui rend la requête hors sujet (« gratuit », « emploi »), ou le terme entier en exact quand c'est une mauvaise performance isolée ;
- **le niveau** : groupe d'annonces si le terme est pertinent pour le client mais mal aiguillé, campagne sinon.

Ce que l'IA ne fait pas : elle ne calcule pas la performance, ne décide pas seule (tu valides ou tu relis l'export), n'écrit pas dans le compte, et n'est pas réentraînée. Elle « apprend » uniquement parce que tes décisions sont ajoutées à ses instructions.

## 7. Les garde-fous

Après la réponse de l'IA, du code vérifie chaque proposition et la corrige si besoin. Une correction est toujours signalée entre crochets dans la raison.

| Situation | Correction appliquée |
|---|---|
| Achat proposé sur Shopping ou Performance Max | Passe en « ignorer » : ces campagnes n'ont pas de mots clés |
| Achat d'un terme déjà ajouté, ou déjà présent en mot clé exact dans le compte | Passe en « ignorer » |
| Achat proposé par l'IA sans signal GOOD | Passe en « ignorer » |
| Groupe d'annonces cible inexistant dans la campagne | Remplacé par « NOUVEAU » |
| Texte à exclure contenant des mots absents du terme | Remplacé par le terme entier en exact |
| Exclusion « large » sur le terme entier | Passe en exact |
| Texte à exclure trop long | Raccourci à 10 mots et 80 caractères |
| Exclusion au niveau groupe sur Performance Max | Passe au niveau campagne |
| L'IA n'a rien répondu pour un terme | Marqué « indécis », jamais exporté |

Par ailleurs, les termes déjà exclus ne sont jamais chargés : ceux que Google marque comme exclus, et ceux dont le texte correspond à une exclusion de la campagne.

Tes propres décisions passent par les mêmes contrôles techniques, à une différence près : toi, tu peux décider d'acheter un terme sans signal GOOD.

## 8. La mémoire par client

Pour chaque compte, l'outil enregistre un fichier contenant :

- l'URL du site, le résumé de l'offre et ce qu'on ne veut pas cibler ;
- l'indicateur de pilotage et tous les seuils ;
- les listes de marques et leurs consignes ;
- les décisions que tu as validées ou corrigées (les 300 dernières), avec ta raison.

Ce fichier est rechargé à chaque session. Les 60 décisions les plus récentes sont transmises à l'IA à chaque analyse.

Emplacement : le dossier `data/clients/` en local, ou un bucket Google Cloud Storage en production (un fichier `<identifiant du compte>.json` par client). Le bucket est versionné, ce qui permet de revenir à un état antérieur.

Ce qui n'est **pas** mémorisé : les termes de recherche, les métriques et les résultats d'une analyse. Ils disparaissent à la fin de la session ; pense à télécharger tes fichiers.

## 9. Les fichiers produits

| Fichier | Contenu | Usage |
|---|---|---|
| `mots_cles_a_ajouter_<compte>.csv` | Campaign, Ad Group, Keyword, Criterion Type (Exact, Phrase, Broad), Status | Import dans Google Ads Editor |
| `exclusions_a_ajouter_<compte>.csv` | Campaign, Ad Group (vide au niveau campagne), Keyword, Criterion Type (ex. Campaign Negative Phrase, Negative Exact) | Import dans Google Ads Editor |
| `groupes_a_creer_<compte>.csv` | Termes rentables sans groupe d'annonces adapté, avec leurs métriques | À traiter à la main |
| `analyse_complete_<compte>.csv` | Tous les termes évalués, métriques, signal, décision, raison, source | Relecture, partage, archive |

Les colonnes Raison, Priorité, Source et les métriques sont là pour ta relecture. À l'import dans Editor, indique de ne pas les importer.

La colonne Source indique l'origine de la décision : « IA » (non relue), « IA validée » (tu as cliqué D'accord), « Humain » (tu as corrigé) ou « Règle » (consigne de marque).

Les exclusions sont dédoublonnées : si dix termes mènent à exclure « gratuit » en expression sur la même campagne, le fichier n'en contient qu'une ligne.

## 10. Limites connues

- **Pas encore éprouvé sur un compte réel.** Le cœur métier et l'interface sont testés sur des données fictives. Les appels à l'API Google Ads et à Gemini restent à valider avec un vrai developer token.
- **Termes Performance Max.** Ils viennent d'une ressource récente de l'API (`campaign_search_term_view`). Si la requête échoue, l'outil affiche un avertissement et continue avec les autres types de campagnes.
- **Pas d'achat de mots clés depuis Shopping ou Performance Max.** Un terme très rentable dans ces campagnes est seulement signalé dans l'analyse complète.
- **Groupes d'annonces candidats limités à la campagne du terme**, et aux 60 premiers groupes de cette campagne.
- **Exclusions existantes vérifiées au niveau campagne seulement**, en comparaison de texte exact. Les listes partagées et les exclusions de groupe d'annonces ne sont pas prises en compte, sauf quand Google marque lui-même le terme comme exclu (Search et Shopping).
- **Un seul compte à la fois**, et pas de Microsoft Advertising.
- **Délai de conversion.** Les jours récents sont sous-estimés si le cycle de vente est long, ce qui peut classer à tort un terme en BAD.
- **Session non persistante.** Recharger la page fait perdre l'analyse en cours et demande une nouvelle connexion. La mémoire du client, elle, est conservée.

## 11. FAQ

### Sécurité et données

**L'outil peut-il modifier mon compte Google Ads ?**
Non. Il n'envoie que des requêtes de lecture. Aucune ligne de code ne crée, modifie ou supprime quoi que ce soit dans Google Ads.

**Quelles données partent chez Gemini ?**
Le résumé du site, ce que tu as écrit dans le contexte, tes décisions mémorisées, et pour chaque terme : son texte, les noms de sa campagne et de son groupe d'annonces, ses métriques, ainsi que des exemples de mots clés des groupes candidats. Vérifie que les conditions d'utilisation de ta clé Gemini conviennent à tes clients avant d'analyser leurs comptes.

**Les données d'un client peuvent-elles se retrouver dans l'analyse d'un autre ?**
Non. Chaque client a son propre fichier de mémoire, et changer de client vide entièrement l'analyse en cours.

**Qui peut utiliser l'application une fois déployée ?**
L'URL est publique par défaut, comme dans l'outil d'origine, mais elle ne donne accès à rien sans une connexion Google ayant accès au MCC. Tu peux restreindre l'accès dans le Terraform.

**Où sont stockés mes identifiants ?**
Dans les variables d'environnement du service. Le jeton de connexion Google reste en mémoire le temps de la session et n'est écrit nulle part.

### Mise en route

**Je n'ai pas de developer token. Que faire ?**
Demande à l'administrateur du MCC de l'agence : le token se trouve dans le Centre API du compte administrateur. Un token en mode test ne lit que des comptes de test ; il faut un accès approuvé par Google pour lire de vrais comptes. En attendant, le mode démo montre l'interface.

**L'abonnement Gemini de l'agence suffit-il ?**
Non. Il faut une clé API, créée dans Google AI Studio, ce qui est distinct d'un abonnement à l'application Gemini.

**Combien coûte une analyse ?**
L'API Google Ads est gratuite. Gemini est facturé à l'usage selon le modèle choisi : compte un appel par paquet de 50 termes, plus un appel par lot d'entraînement. Le champ « Termes max. » plafonne la dépense.

**Puis-je changer de modèle ?**
Oui, avec la variable `GEMINI_MODEL`. Un modèle plus puissant juge mieux les cas ambigus, pour un coût et un temps plus élevés.

**J'obtiens « redirect_uri_mismatch » à la connexion.**
L'URL de l'application n'est pas dans les URI de redirection autorisés du client OAuth, ou `OAUTH_REDIRECT_URI` ne lui correspond pas exactement.

**J'obtiens une erreur de permission ou de developer token au chargement.**
Soit le compte Google connecté n'a pas accès au MCC indiqué dans `MCC_ID`, soit le developer token n'est pas approuvé pour des comptes réels. Le message de l'API est affiché tel quel.

### Résultats

**Pourquoi un terme rentable n'est-il pas proposé à l'achat ?**
Quatre causes possibles : il est déjà en mot clé, il vient de Shopping ou Performance Max, il n'atteint pas tes seuils (nombre de conversions, CPA ou ROAS), ou l'IA le juge sans rapport avec l'offre. La colonne Raison de l'analyse complète le précise.

**Pourquoi acheter un terme qui déclenche déjà mes annonces ?**
Pour le piloter directement : enchère, annonce et page dédiées, et une diffusion qui ne dépend plus de l'interprétation d'un mot clé plus large. Attention, ajouter beaucoup de mots clés exacts très proches fragmente les données ; garde les termes à volume significatif.

**Un terme à mauvaise performance est en « ignorer ». Est-ce une erreur ?**
Pas forcément. Si le terme est au cœur de l'offre, l'IA recommande de ne pas l'exclure et de travailler l'enchère, l'annonce ou la page. Si tu n'es pas d'accord, corrige : elle en tiendra compte.

**Un mot seul en expression à exclure, n'est-ce pas risqué ?**
Si. Exclure « gratuit » en expression bloque toutes les requêtes contenant ce mot. C'est efficace et c'est aussi le type d'exclusion à relire en priorité. Si un client vend réellement quelque chose associé à ce mot, corrige la proposition et explique pourquoi.

**L'outil détecte-t-il les conflits entre exclusions et mots clés ?**
Non, pas dans cette version. Avant d'importer une exclusion en expression, vérifie qu'elle ne bloque pas un mot clé actif. C'est précisément ce que fait Negative Keyword Cleaner : les deux outils se complètent.

**Pourquoi y a-t-il si peu de recommandations ?**
Le plus souvent : période trop courte, minimum d'impressions trop haut, ou seuils trop stricts pour le volume du compte. Regarde la répartition GOOD / BAD / NEUTRAL après le chargement : si presque tout est NEUTRAL, allonge la période.

**Pourquoi les termes Performance Max n'apparaissent-ils pas ?**
Soit la campagne n'a pas de trafic Search sur la période, soit la requête a échoué : dans ce cas un avertissement s'affiche sous le chargement.

**Les résultats sont-ils identiques si je relance l'analyse ?**
Les signaux de performance, les règles de marques et les garde-fous, oui. Les jugements de l'IA peuvent varier légèrement d'une fois à l'autre sur les cas limites.

### Entraînement

**Combien de lots faut-il valider ?**
Il n'y a pas de nombre fixe. Deux ou trois lots suffisent souvent à caler l'IA sur un client ; continue tant que l'accord reste bas ou que les erreurs se répètent. Comme la mémoire est conservée, l'effort diminue d'une session à l'autre.

**L'IA apprend-elle vraiment ?**
Elle n'est pas réentraînée. Tes décisions et tes raisons sont ajoutées à ses instructions, ce qui oriente ses réponses suivantes. C'est le mécanisme de l'outil d'origine.

**À quoi sert d'écrire une raison quand je corrige ?**
C'est l'information la plus utile pour l'IA. « Le client ne vend pas aux particuliers » corrige des dizaines de termes à venir ; une correction sans raison n'en corrige qu'un.

**Je me suis trompé en validant une carte. Comment corriger ?**
Dans cette version, une carte traitée ne se rouvre pas. Corrige la ligne dans le fichier exporté. Si l'erreur risque d'influencer l'IA, supprime l'exemple dans le fichier de mémoire du client.

**Comment remettre à zéro la mémoire d'un client ?**
Supprime son fichier `<identifiant du compte>.json`, dans `data/clients/` en local ou dans le bucket en production. Tu peux aussi l'éditer pour ne retirer que certaines décisions.

**Que deviennent les cartes non traitées si je clique sur « Nouveau lot » ?**
Elles sont remises dans la réserve et reviendront dans un lot suivant ou dans l'analyse du reste.

**Deux personnes peuvent-elles travailler sur le même client en même temps ?**
Chacune a sa propre session, mais la mémoire du client est partagée : la dernière sauvegarde écrase la précédente. Évite de modifier le contexte d'un même client en parallèle.

### Utilisation avancée

**Comment importer les fichiers dans Google Ads Editor ?**
Compte > Importer > Depuis un fichier, puis vérifie la correspondance des colonnes et l'aperçu des modifications avant de valider et de publier. Lors du premier import, contrôle sur quelques lignes que les types de critères sont bien reconnus.

**Que faire du fichier « groupes à créer » ?**
Ce sont des termes rentables qu'aucun groupe d'annonces existant n'accueille bien. À toi de créer la structure, puis d'y ajouter ces mots clés.

**Puis-je faire tourner l'outil automatiquement chaque semaine ?**
Non. Il est conçu pour une session avec validation humaine, comme l'outil d'origine.

**Puis-je l'utiliser sur un compte hors du MCC ?**
Non. Les comptes proposés sont ceux du MCC indiqué dans la configuration.

**L'outil gère-t-il d'autres langues que le français ?**
L'interface et les justifications sont en français. Les termes de recherche peuvent être dans n'importe quelle langue comprise par le modèle ; la détection des marques ignore la casse et les accents.

**Peut-on appliquer les recommandations directement par l'API ?**
Pas dans cette version, par choix : une erreur d'exclusion coûte cher, et le passage par Editor impose une relecture.
