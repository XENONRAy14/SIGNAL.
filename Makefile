.PHONY: up down test logs backup
up:
	docker compose up --build -d
down:
	docker compose down
test:
	docker compose run --rm api pytest -q
logs:
	docker compose logs -f api worker scheduler
backup:
	docker compose exec -T db pg_dump -U signal signal > signal-backup.sql
