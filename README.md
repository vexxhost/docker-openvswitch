# `docker-openvswitch`

This repository contains a `Dockerfile` that builds the latest Open vSwitch
from source, with DPDK support enabled.  It focuses on a few specific things:

- Isolated build stages that minimize final image size
- Isolated network access during build stages to improve reproducibility
  and security
- Downstream patches to improve performance and stability

This image is primarily built to be consumed by [Atmosphere](https://github.com/vexxhost/atmosphere),
however it may be useful for other projects as well.

## Release helper

The release workflow creates or refreshes a GitHub draft release from the pinned
Open vSwitch commit and the local quilt patch stack whenever changes merge to
`main`. The draft can then be promoted in GitHub once reviewed.

The helper describes `OVS_COMMIT` against the upstream Open vSwitch tags and
adds the number of patches listed in `patches/series`. For example, an
`OVS_COMMIT` described as `v3.3.9-4-g...` with two local patches becomes
`v3.3.9-6`.

Run the built-in tests with:

```shell
uv run hack/release.py --self-test
```

To create or refresh the GitHub draft release locally:

```shell
GITHUB_REPOSITORY=vexxhost/docker-openvswitch uv run hack/release.py
```

The release path uses `GITHUB_TOKEN` or `GH_TOKEN` for authentication. The Open
vSwitch source must be checked out at `./ovs`, which the release workflow does
automatically.
