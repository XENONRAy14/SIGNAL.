# SIGNAL — Architecture et décisions

MVP local, monolithe modulaire avec collecte asynchrone. Identité : « Les opportunités à la source ». Palette graphite, vert lime, accents cyan ; typographie système ; favicon radar SVG, aucune ressource CDN.

## Composants

- React 19 + TypeScript + Vite : interface responsive, profil, recommandations, CV, suivi, administration.
- Nginx : fichiers compilés, reverse proxy `/api`, limite d’upload, CSP. Même origine pour cookies et API.
- FastAPI : API synchrone, validation Pydantic, documentation OpenAPI `/api/docs`.
- SQLAlchemy + PostgreSQL : source de vérité. SQLite utilisé uniquement pour tests rapides et développement facultatif.
- Alembic : migration initiale figée ; `alembic check` vérifie les divergences.
- Redis : broker Celery, résultats temporaires, verrous distribués et limitations de débit.
- Celery worker : collecte d’une entreprise par tâche ; scheduler Beat singleton toutes les 5 minutes.

## Données

`users` → `auth_sessions`, `resumes`, `tailored_resumes`, `saved_jobs`.
`companies` → `jobs`, `crawl_runs`. `jobs` → plusieurs `job_sources`.
`http_cache` conserve les représentations et validateurs ETag / Last-Modified.

Tous les champs du modèle Job demandé existent. Les valeurs inconnues restent nulles, listes vides ou `unknown` ; salaire annuel normalisé seulement lorsque la période est explicitement annuelle. Une présence dans la description ne prouve pas qu’une compétence est obligatoire : l’interface parle de « compétences identifiées ».

## Collecte

1. Entreprise issue du catalogue vérifié, d’un agrégateur (découverte automatique), d’un ajout administrateur ou d’un import CSV.
2. Sans page carrière : page d’accueil, liens, sitemap simple, chemins courants ; reconnaissance Greenhouse/Lever/Ashby/SmartRecruiters/Recruitee.
3. Registry `ATSAdapter` : ajout d’un nouveau connecteur sans modifier l’orchestrateur.
4. API publique prioritaire. Fallback conservateur sur JobPosting JSON-LD pour pages HTML ; pas de navigateur headless automatique.
5. HTTP borné : 100 pages/crawl, 8 Mo/réponse, timeout 15 s, 4 redirections max, TLS vérifié, DNS épinglé à une IP publique.
6. `robots.txt` selon RFC 9309 (parseur maison : groupe d’agent le plus spécifique, règle la plus longue, `Allow` à égalité, `*` et `$`), décision ALLOW / BLOCK / RETRY / UNKNOWN, cache `robots_cache` 24 h. 404/410 : aucune restriction ; 401/403 : accès public seulement ; 429/5xx/réseau : `RetryLater` (retry Celery avec backoff), copie de moins de 30 jours réutilisable ; `Disallow` toujours respecté. CAPTCHA/blocages respectés, aucune tentative de contournement.
7. Limitation globale par domaine, minimum 2 secondes ; Crawl-delay respecté jusqu’à 60 s (au-delà, la collecte est refusée).
8. Requêtes conditionnelles. HTTP 304 réutilise le corps en cache ; hash évite les modifications de contenu inutiles.
9. Transaction atomique : aucun job fermé lorsqu’un téléchargement, une pagination ou une normalisation échoue.

### Adaptateurs

| Connecteur | Implémentation | Fermeture automatique |
|---|---|---|
| Greenhouse | Job Board API, contenu inclus | Oui, snapshot réussi |
| Lever + Lever EU | Public Postings API, pagination | Oui, pagination complète |
| Ashby | Public Job Posting API, identifiant dérivé de l’URL si absent | Oui |
| Recruitee | Careers Site API `/api/offers/` | Oui |
| SmartRecruiters | Posting API liste + détails | Oui, seulement si budget suffisant |
| HTML générique | JSON-LD JobPosting, liens internes bornés | Non |
| Workday, Teamtailor, SuccessFactors, Taleo | Points d’extension enregistrés, fallback JSON-LD | Non ; API dédiée non implémentée |

Un board SmartRecruiters trop volumineux pour le budget produit une erreur sans fermeture d’annonces. Pagination incrémentale persistante et budgets par connecteur sont une évolution nécessaire pour les très gros boards.

### Découverte automatique

- `crawling/aggregators.py` : registre `Source` (France Travail, Adzuna, Arbeitnow). Chaque source produit des lots `(offres, curseur)` ; chaque lot est ingéré et commité, le curseur est conservé dans `source_runs`. Budget de 10 minutes par passage, reprise au cycle suivant.
- France Travail : OAuth2 client credentials, fenêtres horaires sur `minCreationDate`/`maxCreationDate`. Plafond de 1 150 résultats par requête : découpage par département, puis par demi-fenêtre jusqu’à 10 minutes. Pagination 150 avec début ≤ 1000.
- France Travail et Adzuna sont des API sous licence, appelées avec les identifiants de l’exploitant via un client borné (timeouts, sans redirection, backoff 429) ; les flux publics sans clé passent par `SafeHTTP` (robots, cadence, cache).
- `crawling/discovery.py` : employeur retrouvé par `name_key` (nom normalisé sans forme juridique) puis domaine ; sinon créé avec `ats_provider='aggregated'` et un domaine synthétique `~clé`. Un employeur déjà suivi en direct avec succès ignore les offres d’agrégateur.
- Sondage ATS par lots (5 min, plus gros employeurs d’abord) : site officiel si connu, puis identifiants dérivés du nom sur Greenhouse, Lever (global/EU), Ashby, Recruitee. Validation obligatoire : nom du board (Greenhouse), `company_name` (Recruitee) ou mention dans les descriptions (Lever, Ashby). Échec : nouvel essai après 30 jours ; aucune réponse définitive : reste en attente.
- Les offres d’agrégateurs sont fermées après `AGGREGATOR_TTL_DAYS` sans nouvelle observation. Attribution affichée (source, date, lien d’origine).

### Cycle de vie et déduplication

- Identité source : entreprise + ATS + external ID, sinon URL canonique.
- Correspondance secondaire par URL, puis entreprise + titre normalisé + localisation + contrat.
- Suppression des paramètres de tracking connus. Historique des URLs/sources dans `job_sources`.
- Une omission dans un snapshot complet réussi → `potentially_closed`.
- Au moins 3 omissions et 48 h depuis la dernière observation → `closed`.
- Retour dans un snapshot → réactivation, compteur à zéro. Expiration explicite → `closed`.
- Matching sémantique approximatif/fuzzy non activé : on évite de fusionner deux postes différents par excès de similarité. Les normalisations d’alias de villes restent à enrichir.

### Planification

6 heures pour les entreprises ayant des offres actives, 24 heures sinon. Priorité Redis/Celery 0 (haute) pour les entreprises actives, 6 sinon. Les données fictives ne sont jamais crawlées. Verrou d’entreprise et verrou d’enqueue, `acks_late`, prefetch 1, timeout de tâche 15 minutes. Jusqu’à 3 retries (30/60/120 s). Tâches orphelines expirées après 2 h, récupérées par le scheduler.

## Recommandation

Score explicable : compétences 45, domaine 15, contrat 15, mode 10, localisation 10, expérience 5. Les poids des préférences non fournies sont exclus. Valeur inconnue pour contrat/mode/expérience : 0,5, indiquée comme information manquante lorsque pertinent. Ce score ne mesure pas une probabilité d’embauche.

Distance Haversine si toutes les coordonnées sont disponibles ; sinon correspondance de ville et avertissement. Pas de géocodage automatique ni de rayon garanti en l’absence de coordonnées. Langues, diplôme, préférences libres sont conservés sans pondération dans cette version.

Tri matching sur les 2 000 offres les plus récentes après filtres. La fenêtre est exposée dans l’API et l’interface. Tri chronologique paginé pour tout le corpus. Étape suivante : index PostgreSQL full-text/pg_trgm et pré-calcul asynchrone des recommandations.

## CV

PDF texte, DOCX et TXT jusqu’à 5 Mo ; 15 pages PDF ; 50 000 caractères. Tables DOCX incluses ; zip décompressé borné. Pas d’OCR. Les fichiers binaires ne sont pas conservés : seul le texte extrait est enregistré.

Adaptation extractive locale : sélectionner les lignes pertinentes, ajouter un encadré ciblé, conserver le CV original intégral. Ne change ni dates ni diplômes, ne rajoute pas de compétences absentes. Le candidat relit puis télécharge DOCX/TXT. La mise en page d’origine n’est pas préservée ; pas de garantie « ATS score » commerciale.

Option IA : consentement par case à cocher, clé serveur seulement. Le modèle reçoit CV + annonce tronquée et ne peut renvoyer que des indices de lignes. Validation de tous les indices ; la sortie finale reste composée d’extraits originaux. En cas d’erreur : pas de fabrication, invitation à utiliser le mode local.

## Sécurité et exploitation

Argon2, sessions opaques (hash en base), cookie HttpOnly/SameSite, CSRF, contrôle d’origine, quotas Redis, droits administrateur, contrôle du propriétaire sur chaque CV/export. DNS et redirections revalidés ; IP de connexion épinglée contre le rebinding ; réseau privé, loopback, link-local et ports non autorisés refusés. Aucun `dangerouslySetInnerHTML`.

Docker expose uniquement Nginx sur `127.0.0.1:8080`. Processus API/worker et Nginx non root. Redis et PostgreSQL restent internes. Pour exposition publique : TLS/reverse proxy, cookie Secure, origine exacte, sauvegardes vérifiées, secrets gérés, quotas persistants par tenant, observabilité et politique de conservation. Aucune certification ou conformité juridique revendiquée.

Le cache HTTP peut contenir les coordonnées publiquement présentes dans une annonce. Ne pas journaliser les CV, mots de passe ou clés. Ne jamais committer `.env`.

## Hors MVP

Emails de vérification et récupération de mot de passe, SSO, facturation, alertes email/push, géocodage mondial, moteur vectoriel, headless, OCR, connecteurs propriétaires authentifiés, tests de charge et haute disponibilité. Le code permet une base maintenable ; la capacité à l’échelle ne doit pas être affirmée sans mesure.
