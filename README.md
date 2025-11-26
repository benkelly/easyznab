# easyznab

FastAPI-based proxy that exposes Easynews global search as a Newznab-compatible API. This makes it possible to plug Easynews into aggregators such as Prowlarr, NZBHydra, or any client that knows how to speak the Newznab protocol.

## Features

- `/api?t=caps` endpoint that reports Newznab capabilities so indexer clients can auto-configure
- `/api?t=search|tvsearch|movie` endpoints that pass the query through to Easynews and return RSS-formatted results
- Simple API key check via the `PROXY_API_KEY` environment variable
- Docker image published to GHCR for both `linux/amd64` (Intel) and `linux/arm64` (Apple Silicon/Graviton) thanks to the included GitHub Actions workflow

## Configuration

| Variable | Description |
| --- | --- |
| `PROXY_API_KEY` | Shared secret that clients must send via the `apikey` query parameter. Defaults to `changeme`—override this before exposing the service. |
| `EASYNEWS_USER` / `EASYNEWS_PASS` |  Easynews credentials. |

## Running Locally

```bash
# install deps (python 3.12+)
pip install fastapi uvicorn httpx

# run the API
PROXY_API_KEY=supersecret EASYNEWS_USER=me EASYNEWS_PASS=pass \
    uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

You can also use the provided Dockerfile:

```bash
make build IMAGE_TAG=dev
PROXY_API_KEY=supersecret EASYNEWS_USER=me EASYNEWS_PASS=pass make run
```

The service listens on port `8080`.

### Prebuilt image (GHCR)

Once GitHub Actions finishes, the repository publishes images to `ghcr.io/benkelly/easyznab`. Run it without building locally:

```bash
docker run --rm -p 8080:8080 \
  -e PROXY_API_KEY=supersecret \
  -e EASYNEWS_USER=me \
  -e EASYNEWS_PASS=pass \
  ghcr.io/benkelly/easyznab:latest
```

Swap in your own secrets or supply them via Docker secrets/compose as needed.

## Helm chart

The repo also contains a Helm chart under `charts/easyznab`. GitHub Actions packages it and pushes releases to `oci://ghcr.io/benkelly/charts/easyznab`.

```bash
helm install easyznab oci://ghcr.io/benkelly/charts/easyznab \
  --version 0.1.0 \
  --set secret.data.PROXY_API_KEY=supersecret \
  --set secret.data.EASYNEWS_USER=me \
  --set secret.data.EASYNEWS_PASS=pass
```

- Override `secret.create=false` and set `secret.name` if you prefer to manage the Kubernetes secret yourself.
- Additional environment variables can be injected via `env` / `envFrom` in `values.yaml`.
- Run `helm show values oci://ghcr.io/benkelly/charts/easyznab --version <tag>` to see every toggle before installing.

## Using with Prowlarr (example)

1. Create a new `Newznab` indexer.
2. Set the URL to `http://<host>:8080/api`.
3. Enter `easyznab` (or anything you prefer) as the name.
4. Put the same API key you configured in `PROXY_API_KEY`.
5. Test the connection; Prowlarr should read the `/caps` output and allow searching.

## GitHub Actions / publishing

Two workflows live under `.github/workflows/`:

- `docker-publish.yml`: builds multi-arch container images (linux/amd64 + linux/arm64) and pushes to `ghcr.io/<owner>/easyznab` on pushes to `main`, `v*` tags, and manual dispatch.
- `helm-publish.yml`: packages the chart in `charts/easyznab` and publishes it as an OCI artifact to `oci://ghcr.io/<owner>/charts` with versions derived from tags (or a `0.0.0-<sha>` prerelease for branch builds).

If you fork this repository, make sure to:

1. Enable GitHub Actions for the repo.
2. Create a `GHCR` personal access token if you need to push outside of the default `GITHUB_TOKEN`.
3. Adjust the image/charts registry values in the workflows if you publish elsewhere.

## License

MIT (feel free to adapt to your needs). Contributions and issues are welcome.
