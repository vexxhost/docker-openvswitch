# `docker-openvswitch`

This repository contains a `Dockerfile` that builds the latest Open vSwitch
from source, with DPDK support enabled.  It focuses on a few specific things:

- Isolated build stages that minimize final image size
- Isolated network access during build stages to improve reproducibility
  and security
- Downstream patches to improve performance and stability

This image is primarily built to be consumed by [Atmosphere](https://github.com/vexxhost/atmosphere),
however it may be useful for other projects as well.
