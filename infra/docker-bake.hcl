# Multi-arch image builds (linux/amd64 + linux/arm64):
#   docker buildx bake -f infra/docker-bake.hcl                      # build all, both platforms
#   docker buildx bake -f infra/docker-bake.hcl --push               # build and push (set REGISTRY)
#   docker buildx bake -f infra/docker-bake.hcl --set '*.platform=linux/arm64' --load   # one arch, local
# arm64 on an x86 machine needs QEMU: docker run --privileged --rm tonistiigi/binfmt --install arm64

variable "REGISTRY" {
  default = "c2b"
}

variable "TAG" {
  default = "dev"
}

group "default" {
  targets = ["api", "worker", "cad-worker", "caddy"]
}

target "_common" {
  context   = "."
  platforms = ["linux/amd64", "linux/arm64"]
}

target "api" {
  inherits   = ["_common"]
  dockerfile = "apps/api/Dockerfile"
  tags       = ["${REGISTRY}/api:${TAG}"]
}

target "worker" {
  inherits   = ["_common"]
  dockerfile = "apps/worker/Dockerfile"
  tags       = ["${REGISTRY}/worker:${TAG}"]
}

target "cad-worker" {
  inherits   = ["_common"]
  dockerfile = "apps/cad-worker/Dockerfile"
  tags       = ["${REGISTRY}/cad-worker:${TAG}"]
}

target "caddy" {
  inherits   = ["_common"]
  dockerfile = "apps/web/Dockerfile"
  tags       = ["${REGISTRY}/caddy:${TAG}"]
}
