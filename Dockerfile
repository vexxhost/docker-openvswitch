# SPDX-FileCopyrightText: © 2025 VEXXHOST, Inc.
# SPDX-License-Identifier: Apache-2.0

ARG FROM=debian:13.1@sha256:fd8f5a1df07b5195613e4b9a0b6a947d3772a151b81975db27d47f093f60c6e6

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
        libxdp-dev \
        pkg-config \
        xz-utils

FROM builder AS dpdk
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        meson \
        python3-pyelftools
ARG DPDK_VERSION=23.11.5
ADD https://fast.dpdk.org/rel/dpdk-${DPDK_VERSION}.tar.xz /src
RUN --network=none tar -xf /src/dpdk-${DPDK_VERSION}.tar.xz -C /src --strip-components=1 \
        && rm /src/dpdk-${DPDK_VERSION}.tar.xz
ARG TARGETARCH
RUN --network=none case "${TARGETARCH}" in \
        amd64) CPU_SET="x86-64-v2" ;; \
        arm64) CPU_SET="armv8-a" ;; \
        arm) CPU_SET="armv7-a" ;; \
        *) CPU_SET="generic" ;; \
    esac && \
    meson setup \
        --prefix=/usr \
        --libdir=lib/$(gcc -print-multiarch) \
        --buildtype=release \
        -Dauto_features=enabled \
        -Ddefault_library=static \
        -Dcpu_instruction_set=${CPU_SET} \
        build
RUN --network=none ninja -C build
RUN --network=none meson test -C build --suite fast-tests
RUN --network=none meson install -C build --destdir /out

FROM builder AS openvswitch
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        g++ \
        libcap-ng-dev \
        make \
        openssl \
        quilt \
        python3
ARG OVS_VERSION=3.3.6
ADD https://www.openvswitch.org/releases/openvswitch-${OVS_VERSION}.tar.gz /src
RUN --network=none tar -xf /src/openvswitch-${OVS_VERSION}.tar.gz -C /src --strip-components=1 \
        && rm /src/openvswitch-${OVS_VERSION}.tar.gz
COPY patches /patches
RUN --network=none \
    QUILT_PATCHES=/patches \
    QUILT_PC=/src/.pc \
    QUILT_PATCH_OPTS="--unified -p1" \
    quilt push -a --fuzz=0 --leave-rejects
COPY --from=dpdk /out /
ARG TARGETARCH
RUN --network=none case "${TARGETARCH}" in \
        amd64) MARCH="x86-64-v2" ;; \
        arm64) MARCH="armv8-a" ;; \
        arm) MARCH="armv7-a" ;; \
        *) MARCH="native" ;; \
    esac && \
    ./configure \
        --prefix=/usr \
        --localstatedir=/var \
        --sysconfdir=/etc \
        --with-dpdk=static \
        CFLAGS="-O2 -march=${MARCH}"
RUN --network=none make -j$(nproc)
RUN --network=none make check TESTSUITEFLAGS=-j$(nproc)
RUN --network=none make install DESTDIR=/out

FROM ${FROM}
ADD --chmod=755 https://github.com/krallin/tini/releases/download/v0.19.0/tini /tini
RUN groupadd -r -g 42424 openvswitch && \
	useradd -r -g openvswitch -u 999 openvswitch
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        iptables \
        jq \
        libatomic1 \
        libarchive13t64 \
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
        libxdp1 \
        python3-netifaces \
        tcpdump && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
COPY --from=openvswitch /out /
USER openvswitch