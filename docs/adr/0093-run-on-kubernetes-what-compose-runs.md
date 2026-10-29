# ADR-0093: Run on Kubernetes what compose runs

- **Status:** Accepted
- **Date:** 2026-10-29

## Context

ADR-0092 readied the server for an orchestrator: probes a pod IP can ask, a drain
under Kubernetes' thirty seconds, and settings read from the environment. It shipped
`compose.yaml` but no manifests, so every cluster deployment was somebody's own.
Running the image on a cluster (k3s 1.34) beside Ollama and SearXNG turned up four
failures, each reproduced there:

- **A Service named `buy-agent` crash-looped the server.** Kubernetes tells every pod
  in a namespace where its Services are, so a Service named `buy-agent` sets
  `BUY_AGENT_PORT=tcp://10.43.53.197:8000`. The server reads that variable as its
  port and refused it as a usage error. SearXNG's entrypoint drops a non-numeric
  `$SEARXNG_PORT` for the same reason.
- **`runAsNonRoot` refused the image.** Its `USER` was a name, `shopper`, and the
  kubelet cannot check that a name is not root: `CreateContainerConfigError`.
- **A pull Job broke later applies.** Compose pulls the model with a one-off job.
  The same Job on Kubernetes read the model from the generated ConfigMap, whose name
  carries a hash of its contents. A Job's pod template is immutable, so any later
  `kubectl apply -k` that changed a setting failed while the Job existed.
- **A provisioned volume belongs to root.** The image takes ownership of its own
  cache directory, but a claim mounted over it is whatever the provisioner made.

## Decision

`deploy/kustomization.yaml`, applied with `kubectl apply -k deploy`, runs the web
tier, Ollama and SearXNG. It sits in `deploy/` rather than `deploy/k8s/` because
kustomize reads files only from below its own directory, and SearXNG's settings are
the file compose mounts. The operator creates the namespace and a Secret named
`buy-agent` holding the token under `api-token`. Nothing in the repository holds the
token. The pods wait for the Secret, since the key is required.

- **Every pod sets `enableServiceLinks: false`.** The server also says where a
  service link came from when `$BUY_AGENT_PORT` holds one, for manifests written
  elsewhere.
- **The image's `USER` is `1000:1000`,** and the web tier's pod runs as that user and
  group, with `fsGroup` making its claim writable.
- **The web tier** runs one replica, replaced rather than rolled, since its caches
  and run journal are on a `ReadWriteOnce` claim. It has a read-only root, `/tmp` in
  memory, no capabilities and no service-account token. Its liveness probe is
  `/healthz` and its readiness probe `/readyz`, with a ten-second timeout over the
  model server's five-second listing. Its grace period is thirty seconds, over the
  twenty-five it drains for.
- **The model is pulled by an init container in the web tier's pod.** It runs the
  Ollama client as the pod's user and pulls only when `ollama show` fails. So the
  server starts once its model is there, as compose waits for its pull job. A
  changed model is pulled on the next start, and a restart with the model already
  there does not ask the registry.
- **The settings are `configMapGenerator` literals.** The hash in the ConfigMap's
  name restarts the web tier when one changes.
- **Ollama** runs one replica, replaced rather than rolled, with its models on a
  claim. The `k8s/gpu` component, turned on by a commented line, gives it a GPU, as
  `compose.gpu.yaml` does.
- **SearXNG** mounts compose's settings file from a generated ConfigMap at the path
  compose mounts it on. Its secret is the token, as in compose.
- **Ollama and SearXNG are compose's images, tag and digest.** Renovate's
  `kubernetes` manager reads `deploy/k8s/`, and grouping each image puts the bump to
  both files in one pull request. The kustomization names the web tier's own image
  by tag.
- **No Ingress, no NetworkPolicy.** TLS is the proxy's job (ADR-0092), and which
  ingress and which network plugin a cluster runs is the cluster's own choice.
  `docs/docker.md` says what each needs.

## Consequences

`kubectl apply -k deploy` gives a cluster what `docker compose up` gives one machine.

What it costs:

- A changed setting or a new image stops the server before the next one starts: a
  minute without it, plus the download when the model changed. More than one replica
  needs a `ReadWriteMany` claim, or a cache and a journal per pod.
- Readiness follows the model server, as ADR-0092 decided. While Ollama does not
  answer, the Service has no endpoint, so the page cannot be reached and cannot
  explain why.
- With the model missing and the registry unreachable the pod does not start, as
  compose's web tier waits on a failed pull job.
- Release 3.0 predates the probes and the token. The manifests need a later
  release, or an image built from the checkout.

What it obliges:

- **A manifest under `deploy/k8s/` is listed in the kustomization:**
  `test_the_kustomization_applies_every_manifest_beside_it`.
- **Every pod turns its service links off:**
  `test_no_pod_is_told_where_the_services_beside_it_are`.
- **The grace period stays above `DRAIN_SECONDS`:**
  `test_kubernetes_gives_a_stopping_server_room_to_finish`. A `preStop` hook would
  count against it.
- **The image's `USER` stays a number, and the pod's user, group and `fsGroup` are
  that number:** `test_kubernetes_runs_the_image_as_the_user_it_was_built_for`.
- **The probes ask what `_PROBES` answers without a token:**
  `test_kubernetes_probes_the_paths_the_server_answers_without_a_token`.
- **The cluster's images are compose's:**
  `test_kubernetes_runs_the_images_compose_runs`. A third copy of an image joins the
  same Renovate group.
- **The token is a required key of a Secret:**
  `test_kubernetes_will_not_start_a_reachable_server_without_a_token`. The
  kustomization never generates it.
- **A server setting a deployment should set** goes in the kustomization's literals
  as well as `compose.yaml`.
