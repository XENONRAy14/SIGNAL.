# Validation — 2 octobre 2026

## Exécuté avec succès

| Vérification | Résultat |
|---|---|
| Suite Python | 41 tests réussis ; un avertissement de dépréciation du TestClient httpx de Starlette |
| TypeScript + build Vite | Réussi |
| `npm audit` après mise à jour | 0 vulnérabilité connue signalée |
| `pip-audit` après mise à jour | 0 vulnérabilité connue signalée sur les dépendances résolues |
| Migration initiale PostgreSQL réelle | Réussie |
| `alembic check` PostgreSQL | Aucune dérive de schéma |
| API sur PostgreSQL | Inscription, profil JSON, recommandations, favoris, entreprises, suppression du compte réussis |
| Redis + Celery + PostgreSQL | Collecte d’une fixture ATS via un vrai worker : tâche réussie, offre persistée, nettoyage réussi |
| Scheduler Celery | Tâche exécutée par le worker avec succès |
| Chromium desktop 1440 × 1080 | Parcours complet réussi, capture dans `radar-desktop.png` |
| Chromium mobile 390 × 844 | Navigation et rendu réussis, pas de débordement horizontal ; capture dans `radar-mobile.png` |

Parcours navigateur réellement effectué : création d’un compte, configuration compétences/domaines/contrat, import CV texte, recherche, score de compatibilité, adaptation locale, téléchargement DOCX, sauvegarde de l’offre, passage en entretien, navigation mobile et déconnexion. Aucune erreur JavaScript de page détectée.

Tests Python : contrôle d’accès propriétaire/admin, sessions/logout, CSRF/origine, validation, quotas, filtres/pagination, profil, favoris, export/suppression du compte, import TXT/DOCX, adaptation sans ajout de compétences, déduplication ID/URL/identité normalisée, mises à jour différentielles, transitions active/potentially_closed/closed, refus de fermeture après échec, expiration, Greenhouse/Lever/Ashby avec fixtures, pagination, découverte ATS, JSON-LD, cache conditionnel, robots, filtrage SSRF et distance.

## Limites de la validation

- **Docker absent dans l’environnement** : le lancement de Compose et les images finales n’ont pas été exécutés. Services démarrés directement pour les essais ; PostgreSQL et Redis étaient de vrais processus temporaires.
- **Collecte Internet non validée de bout en bout** : essai Ashby échoué sur le DNS du réseau d’exécution ; retries Celery observés. La voie proxy a renvoyé un refus pour `robots.txt`, et la collecte a été arrêtée. Aucun blocage n’a été contourné et aucune fausse annonce réelle n’a été ajoutée.
- Le succès de queue utilise un transport ATS **explicitement simulé dans `tests/queue_fixture_worker.py`**, jamais dans l’image ou la commande de production. Il valide l’orchestration et la persistance, pas le réseau d’un fournisseur.
- Connecteurs SmartRecruiters/Recruitee : code fondé sur leurs API documentées ; aucun test réel réseau réussi ici. Les tests de contrat sont à enrichir avec plusieurs boards avant exploitation.
- Pas d’appel payant OpenAI exécuté. Le moteur local a été testé ; la frontière IA n’accepte que des indices de lignes du CV.
- Pas d’OCR, pas d’essai d’envoi d’une candidature, pas de paiement et pas de test de charge.
- Le rendu et les parcours ont été testés sur Chromium ; autres navigateurs non testés.
- Audits npm/pip ponctuels : ils ne garantissent ni absence de faille applicative ni sécurité future. Images système Docker non auditées ici.

Les données de démonstration sont fictives. Les captures ont été prises sur la base locale de test ; le nombre de sources peut inclure l’entreprise de l’essai réseau bloqué. Cette base et les comptes de test ne sont pas inclus dans l’archive.
