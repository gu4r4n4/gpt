# Makefile for backend operations

.PHONY: cleanup-batches create-vector-store

cleanup-batches:
	python backend/scripts/expire_and_cleanup_batches.py

create-vector-store:
	@if [ -z "$(ORG_ID)" ]; then echo "Usage: make create-vector-store ORG_ID=1"; exit 1; fi
	export ORG_ID=$(ORG_ID) && python backend/scripts/create_vector_store.py
