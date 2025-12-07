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

test-iter2:
	./test_iteration2.sh

test-iter2-concurrent:
	python3 test_iteration2_concurrent.py


