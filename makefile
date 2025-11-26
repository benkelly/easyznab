IMAGE_NAME ?= ghcr.io/$(shell echo "$$(git config --get github.user 2>/dev/null || echo "your-gh-username")")/easyznab
IMAGE_TAG ?= dev
CONTAINER_NAME ?= easyznab

build:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

run:
	docker run --rm \
		--name $(CONTAINER_NAME) \
		-p 8080:8080 \
		-e PROXY_API_KEY="changeme" \
		-e EASYNEWS_USER="your_easynews_username" \
		-e EASYNEWS_PASS="your_easynews_password" \
		$(IMAGE_NAME):$(IMAGE_TAG)

shell:
	docker run --rm -it \
		--name $(CONTAINER_NAME)-shell \
		-p 8080:8080 \
		-e PROXY_API_KEY="changeme" \
		-e EASYNEWS_USER="your_easynews_username" \
		-e EASYNEWS_PASS="your_easynews_password" \
		$(IMAGE_NAME):$(IMAGE_TAG) /bin/sh

push:
	docker push $(IMAGE_NAME):$(IMAGE_TAG)

clean:
	-docker rm -f $(CONTAINER_NAME) 2>/dev/null || true
	-docker rmi $(IMAGE_NAME):$(IMAGE_TAG) 2>/dev/null || true

