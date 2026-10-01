.PHONY: up down logs restart migrate seed test-backend test-frontend lint build-backend build-frontend

# --- Docker Compose lifecycle --------------------------------------------
up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

restart:
	docker compose restart

# --- Database (migrations wired since Phase 2; seed data since Phase 2) --
migrate:
	cd backend && alembic upgrade head

seed:
	cd backend && python -m app.seed

# --- Tests -----------------------------------------------------------------
test-backend:
	cd backend && python -m pytest -q

test-frontend:
	cd frontend && npm run typecheck && npm run lint

# --- Lint / format ----------------------------------------------------------
lint:
	cd backend && ruff check app && black --check app
	cd frontend && npm run lint

build-backend:
	cd backend && docker build -f ../infrastructure/backend.Dockerfile -t gainzalgo-backend .

build-frontend:
	cd frontend && docker build -f ../infrastructure/frontend.Dockerfile -t gainzalgo-frontend .
