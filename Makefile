BINARY := mojave-server
IMAGE  := syntaf/mojave-specimen-review
TAG    ?= $(shell git rev-parse HEAD)

.PHONY: run test vet build image deploy photos clean

## run: serve locally on :8080, read-only (no OAuth configured)
run:
	DB_PATH=./data/mojave.db go run .

## run-auth: serve locally with sign-in enabled; export GITHUB_CLIENT_ID/SECRET first
run-auth:
	DB_PATH=./data/mojave.db BASE_URL=http://localhost:8080 \
	SESSION_KEY=$${SESSION_KEY:-dev-only-not-a-secret} \
	ALLOWED_USERS=$${ALLOWED_USERS:-Syntaf} go run .

test:
	go test ./... -race

vet:
	go vet ./...

build:
	CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o $(BINARY) .

image:
	docker build -t $(IMAGE):$(TAG) .

## deploy: manual fallback for the GitHub Actions workflow
deploy:
	helm upgrade --install mojave ./k8s/mojave \
		--namespace dnd --atomic --timeout 5m \
		--set image.tag=$(TAG) \
		$(if $(GH_OAUTH_CLIENT_ID),--set secrets.githubClientId=$(GH_OAUTH_CLIENT_ID)) \
		$(if $(GH_OAUTH_CLIENT_SECRET),--set secrets.githubClientSecret=$(GH_OAUTH_CLIENT_SECRET)) \
		$(if $(SESSION_KEY),--set secrets.sessionKey=$(SESSION_KEY))

## photos: re-source plant photographs from Wikimedia Commons (needs network + Pillow)
photos:
	python3 tools/source_photos.py refresh

clean:
	rm -f $(BINARY)
	rm -rf data
