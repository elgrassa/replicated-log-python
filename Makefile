up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

smoke:
	./scripts/verify.sh

test:
	./run_all_tests.sh


