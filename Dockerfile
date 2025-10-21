# SPDX-FileCopyrightText: © 2025 VEXXHOST, Inc.
# SPDX-License-Identifier: Apache-2.0

ARG FROM=debian:13.1@sha256:72547dd722cd005a8c2aa2079af9ca0ee93aad8e589689135feaed60b0a8c08d

FROM ${FROM} AS builder
WORKDIR /src
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        gcc \
        libarchive-dev \
        libbpf-dev \
        libbsd-dev \
        libc6-dev \
        libfdt-dev \
        libibverbs-dev \
        libisal-dev \
        libjansson-dev \
        libnuma-dev \
        libpcap-dev \
        libssl-dev \
        libunbound-dev \
        libxdp-dev \
        pkg-config \
        xz-utils

FROM builder AS dpdk
WORKDIR /src/dpdk
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        meson \
        python3-pyelftools
ARG DPDK_VERSION=23.11.5
ADD https://fast.dpdk.org/rel/dpdk-${DPDK_VERSION}.tar.xz /src
RUN --network=none tar -xf /src/dpdk-${DPDK_VERSION}.tar.xz -C /src/dpdk --strip-components=1 \
        && rm /src/dpdk-${DPDK_VERSION}.tar.xz
RUN --network=none \
    meson setup \
        --prefix=/usr \
        --libdir=lib/$(gcc -print-multiarch) \
        --buildtype=plain \
        -Ddisable_apps="*" \
        -Denable_libs="bbdev,bitratestats,bpf,cmdline,cryptodev,dmadev,gro,gso,hash,ip_frag,latencystats,member,meter,metrics,pcapng,pdump,security,stack,vhost" \
        -Denable_drivers="baseband/acc,bus/auxiliary,bus/pci,bus/vdev,bus/vmbus,common/iavf,common/mlx5,common/nfp,mempool/ring,net/bnxt,net/e1000,net/enic,net/failsafe,net/i40e,net/iavf,net/ice,net/ixgbe,net/mlx5,net/netvsc,net/nfp,net/qede,net/ring,net/tap,net/vdev_netvsc,net/vhost,net/virtio" \
        -Ddefault_library=static \
        -Dcpu_instruction_set=generic \
        -Dmax_ethports=1024 \
        -Dmax_numa_nodes=8 \
        build
RUN --network=none ninja -C build
RUN --network=none meson test -C build --suite fast-tests
RUN --network=none meson install -C build --destdir /out/dpdk

FROM builder AS openvswitch
WORKDIR /src/ovs
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        autoconf \
        automake \
        g++ \
        libcap-ng-dev \
        libtool \
        make \
        openssl \
        python3 \
        quilt
ARG OVS_COMMIT=adcf7b4687e20c52f226724ddc945a66149a9305
ADD https://github.com/openvswitch/ovs.git#${OVS_COMMIT} /src/ovs
COPY patches /patches
RUN --network=none \
    QUILT_PATCHES=/patches \
    QUILT_PC=/src/.pc \
    QUILT_PATCH_OPTS="--unified -p1" \
    quilt push -a --fuzz=0 --leave-rejects
RUN --network=none ./boot.sh
COPY --from=dpdk /out/dpdk /
RUN --network=none \
    ./configure \
        --prefix=/usr \
        --localstatedir=/var \
        --sysconfdir=/etc \
        --with-dpdk=static
RUN --network=none make -j$(nproc)
RUN --network=none make check TESTSUITEFLAGS=-j$(nproc)
RUN --network=none make install DESTDIR=/out/ovs

FROM ${FROM}
ADD --chmod=755 https://github.com/krallin/tini/releases/download/v0.19.0/tini /tini
RUN groupadd -r -g 42424 openvswitch && \
    useradd -r -g openvswitch -u 42424 openvswitch
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        iproute2 \
        iptables \
        jq \
        libarchive13t64 \
        libatomic1 \
        libbpf1 \
        libbsd0 \
        libc6 \
        libfdt1 \
        libibverbs-dev \
        libisal2 \
        libjansson4 \
        libnuma1 \
        libpcap0.8t64 \
        libssl3t64 \
        libunbound8 \
        libxdp1 \
        python3-netifaces \
        tcpdump && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
ARG OVSINIT_VERSION=0.2.0
ARG TARGETOS
ARG TARGETARCH
ADD --chmod=755 https://github.com/vexxhost/ovsinit/releases/download/v${OVSINIT_VERSION}/ovsinit_v${OVSINIT_VERSION}_${TARGETOS}_${TARGETARCH} /usr/bin/ovsinit
COPY --from=openvswitch /out/ovs /
